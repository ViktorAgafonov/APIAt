"""Оркестрация конвейера: gateway -> parser -> planner -> workflow -> email."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .config import Settings, get_settings
from .gateway.imap_client import ImapClient
from .gateway.security import is_authorized
from .intent.parser import IntentParser
from .intent.router import LlmAllProvidersFailedError
from .intent.self_corrector import SelfCorrector, try_apply_and_verify, _probe_llm
from .models.base import TaskStatus, TaskType
from .models.email import IncomingMail, OutgoingMail
from .skills.builder import SkillBuilder
from .skills.chain import ChainPlanner, ChainRunner, SkillChain
from .storage.repositories import Storage
from .tools.email_tool import EmailSender
from .tools.registry import ToolRegistry
from .logs.mail_ring import MailRingLog
from .utils.logging import get_logger
from .workflow.engine import WorkflowEngine

logger = get_logger(__name__)

# Ключевые слова для команды переключения/настройки LLM от оператора
_LLM_CMD_RE = re.compile(
    r"(переключи\s*llm|смени\s*нейромодель|switch\s*llm|llm\s*config)",
    re.IGNORECASE,
)

# Список LLM провайдеров: "список llm" / "llm status"
_LLM_LIST_RE = re.compile(
    r"(список\s*llm|llm\s*status|статус\s*llm|покажи\s*llm)",
    re.IGNORECASE,
)

# Команда самообновления кода с GitHub
_UPDATE_CMD_RE = re.compile(
    r"(обнови\s*код|update\s*code|git\s*pull)",
    re.IGNORECASE,
)

# Команда пересборки sandbox-образа
_UPDATE_SANDBOX_RE = re.compile(
    r"(обнови\s*среду|update\s*sandbox|rebuild\s*sandbox)",
    re.IGNORECASE,
)

# Команда самообучения: "самообучись: <описание>"
_LEARN_CMD_RE = re.compile(
    r"(самообучись|learn)\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)

# Закрепление навыка: "закрепи навык <имя>" (обратная совместимость: подтверди навык)
_CONFIRM_SKILL_RE = re.compile(
    r"(закрепи\s*навык|подтверди\s*навык|confirm\s*skill)\s*[:\-]?\s*(\S+)",
    re.IGNORECASE,
)

# Явный запуск навыка по имени: "запусти навык <имя>" / "run skill <имя>"
_RUN_SKILL_RE = re.compile(
    r"(запусти\s*навык|выполни\s*навык|run\s*skill)\s*[:\-]?\s*(\S+)",
    re.IGNORECASE,
)

# Список навыков
_LIST_SKILLS_RE = re.compile(
    r"(список\s*навыков|list\s*skills?|show\s*skills?)",
    re.IGNORECASE,
)

# Запуск цепочки: "выполни цепочку <имя>: key=val key2=val2"
_RUN_CHAIN_RE = re.compile(
    r"(выполни\s*цепочку|run\s*chain)\s*[:\-]?\s*(\S+)([^\n]*)",
    re.IGNORECASE,
)

# Сохранить цепочку: "сохрани цепочку <имя>"
_SAVE_CHAIN_RE = re.compile(
    r"(сохрани\s*цепочку|save\s*chain)\s*[:\-]?\s*(\S+)",
    re.IGNORECASE,
)

# Построить цепочку через LLM: "цепочка: <задача>"
_CHAIN_TASK_RE = re.compile(
    r"(цепочка|chain)\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)

# Анализ писем / рекомендации
_ANALYZE_RE = re.compile(
    r"(анализ\s*писем|analyse?\s*mails?|разбор\s*запросов)",
    re.IGNORECASE,
)
_RECOMMENDATIONS_RE = re.compile(
    r"(рекомендаци[ия]|статус\s*агента|agent\s*status|recommendations?)",
    re.IGNORECASE,
)

# Управление whitelist
_WL_ADD_RE = re.compile(
    r"(добавь\s+в\s+whitelist|whitelist\s*add|добавь\s+доступ)\s*[:\-]?\s*(\S+@\S+)",
    re.IGNORECASE,
)
_WL_REMOVE_RE = re.compile(
    r"(убери\s+(из\s+)?whitelist|whitelist\s*remove|убери\s+доступ)\s*[:\-]?\s*(\S+@\S+)",
    re.IGNORECASE,
)
_WL_LIST_RE = re.compile(
    r"(покажи\s+whitelist|список\s+whitelist|whitelist\s*list|кто\s+имеет\s+доступ)",
    re.IGNORECASE,
)

# Помощь
_HELP_RE = re.compile(
    r"(помощь|инструкции|help|commands?)",
    re.IGNORECASE,
)

# Браузерная авторизация: "авторизуйся: url=... логин=... пароль=..."
_BROWSER_AUTH_RE = re.compile(
    r"(авторизуйся|войди|browser\s*auth|login)\s*[:\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Многошаговая авторизация: "многошаговая авторизация: url=..." + список шагов
_BROWSER_MULTI_AUTH_RE = re.compile(
    r"(многошаговая\s+авторизация|multi[-\s]?step\s+auth)\s*[:\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Скриншот страницы: "скриншот https://..." / "screenshot https://..."
_SCREENSHOT_RE = re.compile(
    r"(скриншот|screenshot)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Ответ на запрос подтверждения отправки: "да" / "zip" / "zip 3"
_CONFIRM_SEND_RE = re.compile(
    r"^\s*(да|yes|zip(?:\s+(\d+))?)\s*$",
    re.IGNORECASE,
)

# Команда управления лимитами: "лимит" / "лимит body_limit=50000"
_LIMIT_CMD_RE = re.compile(
    r"(лимит|limits?)\s*[:\-]?",
    re.IGNORECASE,
)

# Анализ логов сервера: "логи сервера" / "server logs"
_SERVER_LOG_RE = re.compile(
    r"(логи\s*сервера|server\s*logs?|системные\s*логи)",
    re.IGNORECASE,
)

# Анализ логов приложения: "логи приложения" / "app logs"
_APP_LOG_RE = re.compile(
    r"(логи\s*приложения|app\s*logs?|логи\s*агента)",
    re.IGNORECASE,
)


def _strip_quotations(text: str) -> str:
    """Убирает цитаты предыдущих писем из ответного письма.

    Удаляет:
    - строки, начинающиеся с '>'
    - блоки после '-----Original Message-----'
    - блоки после 'On ... wrote:'
    """
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(">"):
            continue
        if stripped.startswith("-----"):
            break
        if re.match(r"On\s+.*\s+wrote:\s*$", stripped, re.IGNORECASE):
            break
        cleaned.append(line)
    # Убираем хвост из пустых строк
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()


class Agent:
    """Сборка компонентов APIAt и обработка входящих писем."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.db_path)
        self.imap = ImapClient(self.settings)
        self.sender = EmailSender(self.settings)
        self.mail_ring = MailRingLog(self.settings.data_dir / "logs")
        self._pending_chains: dict[str, SkillChain] = {}  # run_id -> chain (ожидают сохранения)
        self._pending_chains_limit = 10
        self._current_mail: IncomingMail | None = None  # для thread-заголовков в _safe_send
        self._init_llm_components()

    def _init_llm_components(self) -> None:
        """Пересоздаёт компоненты, зависящие от LLM-настроек."""
        self.parser = IntentParser(self.settings)
        self.registry = ToolRegistry(self.settings.data_dir, llm_router=self.parser.router, storage=self.storage)
        self.engine = WorkflowEngine(self.registry, self.settings.db_path)
        self.skill_builder = SkillBuilder(self.settings, self.settings.data_dir / "skills")
        self.chain_runner = ChainRunner(self.settings.data_dir / "skills", self.settings.data_dir)
        self.chain_planner = ChainPlanner(
            self.skill_builder._router, self.settings.data_dir / "skills"
        )

    async def process_mail(self, mail: IncomingMail) -> None:
        """Полный цикл обработки одного письма."""
        self._current_mail = mail
        # Защита от повторной обработки
        if self.storage.is_mail_processed(mail.message_id):
            logger.info("Письмо %s уже обработано, пропуск", mail.message_id)
            return

        # Безопасность: whitelist (env + БД) + секретный токен
        if not is_authorized(mail, self._effective_whitelist(), self.settings.secret_token):
            logger.warning("Письмо от %s отклонено (нет доступа/токена)", mail.sender)
            self.storage.mark_mail_processed(mail.message_id, mail.sender)
            return

        self.storage.mark_mail_processed(mail.message_id, mail.sender)

        # Очищаем тело от цитат предыдущих писем
        body = _strip_quotations(mail.body)
        # Нормализуем неразрывные пробелы (\xa0 от inbox.ru и др.) → обычный пробел
        body = body.replace("\xa0", " ").replace("\u2009", " ").replace("\u200b", "")
        # Удаляем секретный токен из тела — он нужен только для авторизации
        if self.settings.secret_token:
            body = re.sub(
                rf"(?i)\b{re.escape(self.settings.secret_token)}\b[,\s]*",
                "",
                body,
            )
            body = body.strip()
            subject = re.sub(
                rf"(?i)\b{re.escape(self.settings.secret_token)}\b[,\s]*",
                "",
                mail.subject,
            ).strip()
        else:
            subject = mail.subject
        mail = mail.model_copy(update={"body": body, "subject": subject})
        logger.debug("Тело письма (repr): %r", body[:200])

        # Сохраняем письмо в историю переписки (после очистки от токена)
        self.storage.save_mail_thread(
            message_id=mail.message_id,
            sender=mail.sender,
            subject=mail.subject,
            body=mail.body,
            refs=mail.references,
            direction="in",
        )

        # Команда оператора: обновить код с GitHub
        if _UPDATE_CMD_RE.search(mail.body):
            await self._handle_update_command(mail)
            return

        # Команда оператора: пересборка sandbox-образа
        if _UPDATE_SANDBOX_RE.search(mail.body):
            await self._handle_update_sandbox(mail)
            return

        # Команда оператора: самообучение
        learn_match = _LEARN_CMD_RE.search(mail.body)
        if learn_match:
            await self._handle_learn_command(mail, learn_match.group(2).strip())
            return

        # Команда оператора: подтвердить навык
        confirm_match = _CONFIRM_SKILL_RE.search(mail.body)
        if confirm_match:
            await self._handle_confirm_skill(mail, confirm_match.group(2).strip())
            return

        # Команда оператора: список навыков
        if _LIST_SKILLS_RE.search(mail.body):
            self._handle_list_skills(mail)
            return

        # Команда: запустить цепочку по имени
        run_chain_m = _RUN_CHAIN_RE.search(mail.body)
        if run_chain_m:
            name = run_chain_m.group(2).strip()
            params_str = run_chain_m.group(3).strip()
            await self._handle_run_chain(mail, name, _parse_inline_params(params_str))
            return

        # Команда: сохранить последнюю построенную цепочку
        save_chain_m = _SAVE_CHAIN_RE.search(mail.body)
        if save_chain_m:
            await self._handle_save_chain(mail, save_chain_m.group(2).strip())
            return

        # Команда: построить цепочку через LLM
        chain_task_m = _CHAIN_TASK_RE.search(mail.body)
        if chain_task_m:
            await self._handle_chain_task(mail, chain_task_m.group(2).strip())
            return

        # Команда оператора: список LLM провайдеров
        if _LLM_LIST_RE.search(mail.body):
            await self._handle_llm_status(mail)
            return

        # Команда оператора: переключить/настроить LLM
        if _LLM_CMD_RE.search(mail.body):
            await self._handle_llm_config_command(mail)
            return

        # Анализ писем + рекомендации
        if _ANALYZE_RE.search(mail.body):
            await self._handle_analyze_mails(mail)
            return

        if _RECOMMENDATIONS_RE.search(mail.body):
            self._handle_show_recommendations(mail)
            return

        # Управление whitelist
        wl_add_m = _WL_ADD_RE.search(mail.body)
        if wl_add_m:
            self._handle_whitelist_add(mail, wl_add_m.group(2).strip())
            return

        wl_rm_m = _WL_REMOVE_RE.search(mail.body)
        if wl_rm_m:
            self._handle_whitelist_remove(mail, wl_rm_m.group(3).strip())
            return

        if _WL_LIST_RE.search(mail.body):
            self._handle_whitelist_list(mail)
            return

        # Команда: многошаговая браузерная авторизация
        multi_auth_m = _BROWSER_MULTI_AUTH_RE.search(mail.body)
        if multi_auth_m:
            await self._handle_browser_multi_auth(mail, multi_auth_m.group(2).strip())
            return

        # Команда: браузерная авторизация
        auth_m = _BROWSER_AUTH_RE.search(mail.body)
        if auth_m:
            await self._handle_browser_auth(mail, auth_m.group(2).strip())
            return

        # Команда: скриншот страницы
        shot_m = _SCREENSHOT_RE.search(mail.body)
        if shot_m:
            await self._handle_screenshot(mail, shot_m.group(2).strip())
            return

        # Ответ на запрос подтверждения отправки (да / zip / zip N)
        confirm_m = _CONFIRM_SEND_RE.search(mail.body.strip())
        if confirm_m:
            await self._handle_send_confirmation(mail, confirm_m)
            return

        # Команда: управление лимитами
        if _LIMIT_CMD_RE.search(mail.body):
            self._handle_limit_command(mail)
            return

        # Анализ логов сервера
        if _SERVER_LOG_RE.search(mail.body):
            await self._handle_server_logs(mail)
            return

        # Анализ логов приложения
        if _APP_LOG_RE.search(mail.body):
            await self._handle_app_logs(mail)
            return

        # Помощь
        if _HELP_RE.search(mail.body):
            self._handle_help(mail)
            return

        # Явный запуск навыка: "запусти навык <имя>"
        run_skill_m = _RUN_SKILL_RE.search(mail.body)
        if run_skill_m:
            await self._handle_run_skill(mail, run_skill_m.group(2).strip())
            return

        # Нечёткое совпадение с именем закреплённого навыка (точно, без LLM)
        matched_skill = self._match_skill(mail.body)
        if matched_skill:
            await self._handle_run_skill(mail, matched_skill)
            return

        started = time.monotonic()

        try:
            # Контекст треда: предыдущие письма в переписке
            thread = self.storage.get_thread_history(mail.references, limit=6)
            thread_context = self._format_thread_context(thread)

            task = await self.parser.parse(
                mail,
                skills=self.skill_builder.list_confirmed(),
                chains=self.chain_runner.list_chains(),
                thread_context=thread_context,
            )
            # Прокидываем пути вложений в задачу если тип поддерживает
            if mail.attachments and hasattr(task, "input_attachments"):
                task.input_attachments = [a.path for a in mail.attachments if a.path]

            # LLM решил, что нужно запустить навык/цепочку — выполняем напрямую
            if task.type == TaskType.SKILL:
                await self._handle_run_skill(mail, task.skill_name)
                return
            if task.type == TaskType.CHAIN:
                await self._handle_run_chain(mail, task.chain_name, task.params)
                return

        except LlmAllProvidersFailedError as exc:
            logger.error("Все LLM-провайдеры недоступны: %s", exc)
            self.mail_ring.push(mail, task_type=None, status="FAILED:llm_unavailable", secret_token=self.settings.secret_token)
            self._reply_error(
                mail,
                f"Все LLM-провайдеры недоступны:\n{exc}\n\n"
                f"Отправьте команду 'переключи LLM' с новыми параметрами.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка парсинга интента")
            self.mail_ring.push(mail, task_type=None, status="FAILED:parse_error", secret_token=self.settings.secret_token)
            self._reply_error(mail, f"Не удалось разобрать задачу: {exc}")
            return

        self.storage.save_task(
            task.task_id, task.type.value, TaskStatus.PARSED.value, task.model_dump(mode="json")
        )
        self.storage.update_status(task.task_id, TaskStatus.PARSED)

        try:
            state = await self.engine.run(task)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка workflow")
            self.storage.update_status(task.task_id, TaskStatus.FAILED, str(exc))
            self.mail_ring.push(mail, task_type=task.type.value, status="FAILED:workflow_error", secret_token=self.settings.secret_token)
            self._reply_error(mail, f"Ошибка выполнения: {exc}", task_name=task.type.value)
            return

        status = state.get("status", TaskStatus.FAILED.value)
        result = state.get("result", {})
        elapsed = time.monotonic() - started

        self.storage.update_status(task.task_id, status)
        self.storage.save_result(
            task.task_id,
            summary=result.get("summary", ""),
            data=result.get("data", {}),
            metrics={"execution_time": round(elapsed, 2)},
        )
        self.mail_ring.push(mail, task_type=task.type.value, status=status, secret_token=self.settings.secret_token)
        self._reply_result(mail, task.type.value, status, result, elapsed)

    async def _handle_update_sandbox(self, mail: IncomingMail) -> None:
        """Пересобирает Docker-образ apiat-sandbox из Dockerfile."""
        dockerfile = Path(__file__).parent.parent / "docker" / "skill-sandbox.Dockerfile"
        lines: list[str] = []

        if not dockerfile.exists():
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: обнови среду — ОШИБКА",
                body=f"Dockerfile не найден: {dockerfile}",
                in_reply_to=mail.message_id,
            ))
            return

        lines.append(f"Dockerfile: {dockerfile.name}")
        r = subprocess.run(
            ["docker", "build", "--no-cache=false", "-q",
             "-f", str(dockerfile), "-t", "apiat-sandbox:latest",
             str(dockerfile.parent)],
            capture_output=True, text=True, timeout=300,
        )
        lines.append(f"docker build: {'OK' if r.returncode == 0 else 'ОШИБКА'}")
        if r.returncode == 0:
            image_id = r.stdout.strip().replace("sha256:", "")[:12]
            lines.append(f"Image ID: {image_id}")
        else:
            lines.append(r.stderr.strip()[-800:])

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: sandbox-образ {'обновлён' if r.returncode == 0 else 'ОШИБКА'}",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

    async def _handle_update_command(self, mail: IncomingMail) -> None:
        """git pull + pip install + systemctl restart apiat.

        Выполняется только если письмо авторизовано (whitelist + токен).
        Перед обновлением делает snapshot текущего HEAD для отката.
        """
        project_dir = Path(__file__).parent.parent
        lines: list[str] = []

        def _run(cmd: list[str], cwd=project_dir) -> tuple[int, str]:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
            return r.returncode, (r.stdout + r.stderr).strip()

        # Сохраняем текущий HEAD для возможного отката
        rc, old_head = _run(["git", "rev-parse", "HEAD"])
        old_head = old_head.strip()
        lines.append(f"Текущий HEAD: {old_head[:12]}")

        # git pull
        rc, out = _run(["git", "pull", "origin", "main"])
        lines.append(f"git pull: {'OK' if rc == 0 else 'ОШИБКА'}\n{out}")
        if rc != 0:
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: обновление FAILED",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return

        rc, new_head = _run(["git", "rev-parse", "HEAD"])
        new_head = new_head.strip()
        if old_head == new_head:
            lines.append("Код уже актуален, перезапуск не требуется.")
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: уже актуален",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return
        lines.append(f"Новый HEAD: {new_head[:12]}")

        # pip install (только если изменился requirements.txt)
        venv_pip = project_dir / ".venv" / "bin" / "pip"
        if not venv_pip.exists():
            venv_pip = project_dir / ".venv" / "Scripts" / "pip"
        rc, out = _run([str(venv_pip), "install", "-r", "requirements.txt", "-q"])
        lines.append(f"pip install: {'OK' if rc == 0 else 'ОШИБКА'}\n{out[:300]}")
        if rc != 0:
            # Откатываем git
            _run(["git", "checkout", old_head])
            lines.append(f"Откат к {old_head[:12]} выполнен.")
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: обновление FAILED (откат)",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return

        # Сначала отправляем письмо, затем перезапускаем через 5 сек в фоне.
        # Если делать наоборот — systemctl restart убивает процесс до отправки.
        lines.append("Перезапуск сервиса через 5 сек...")
        logger.info("Самообновление: %s -> %s", old_head[:12], new_head[:12])
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: обновлён до {new_head[:12]}",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

        # Запускаем restart в фоне после задержки — письмо уже ушло
        subprocess.Popen(
            ["bash", "-c", "sleep 5 && systemctl restart apiat"],
            start_new_session=True,
        )

    async def _handle_learn_command(self, mail: IncomingMail, user_prompt: str) -> None:
        """Запускает цикл самообучения: генерация → ревью → sandbox → валидация."""
        logger.info("Самообучение по запросу: %s", user_prompt[:80])
        result = await self.skill_builder.build(user_prompt)

        steps_text = "\n".join(result.steps)
        if not result.success:
            body = (
                f"Навык не получен: {result.error}\n\n"
                f"Шаги выполнения:\n{steps_text}"
            )
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: навык не получен",
                body=body,
                in_reply_to=mail.message_id,
            ))
            return

        body = (
            f"Навык готов к проверке: <b>{result.skill_name}</b>\n\n"
            f"Шаги выполнения:\n{steps_text}\n\n"
            f"--- Вывод навыка ---\n{result.sandbox_output}\n\n"
            f"Если результат вас устраивает, ответьте:\n"
            f"закрепи навык {result.skill_name}"
        )
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: навык '{result.skill_name}' ожидает подтверждения",
            body=body,
            in_reply_to=mail.message_id,
        ))

    async def _handle_chain_task(self, mail: IncomingMail, task: str) -> None:
        """LLM строит план цепочки из доступных навыков и выполняет её."""
        logger.info("Построение цепочки: %s", task[:80])
        params = _parse_inline_params(task)
        chain = await self.chain_planner.plan(task, params)
        if not chain or not chain.steps:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: не удалось построить цепочку",
                body=(
                    "Не найдено подходящих навыков для задачи.\n"
                    f"Доступные навыки: {', '.join(self.skill_builder.list_confirmed()) or 'нет'}"
                ),
                in_reply_to=mail.message_id,
            ))
            return

        run_result = self.chain_runner.run(chain, params, max_steps=self.settings.max_chain_steps)
        report = run_result.report()

        # Храним цепочку для возможного сохранения
        self._pending_chains[chain.name] = chain
        # Вытесняем старые если превышен лимит
        while len(self._pending_chains) > self._pending_chains_limit:
            oldest_key = next(iter(self._pending_chains))
            del self._pending_chains[oldest_key]

        steps_desc = "\n".join(
            f"  {i+1}. {s.skill}: {s.description}" for i, s in enumerate(chain.steps)
        )
        body = (
            f"Цепочка выполнена ({'OK' if run_result.success else 'ОШИБКА'}).\n\n"
            f"План шагов:\n{steps_desc}\n\n"
            f"Результат:\n{report}\n\n"
            f"Если цепочка работает правильно, ответьте чтобы закрепить её:\n"
            f"сохрани цепочку {chain.name}"
        )
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: цепочка '{chain.name}'",
            body=body,
            in_reply_to=mail.message_id,
        ))

    async def _handle_run_chain(self, mail: IncomingMail, name: str, params: dict) -> None:
        """Runs a saved .chain.json by name."""
        chain = self.chain_runner.load_chain(name)
        if not chain:
            chains = self.chain_runner.list_chains()
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject=f"APIAt: цепочка '{name}' не найдена",
                body=f"Доступные цепочки: {', '.join(chains) or 'нет'}",
                in_reply_to=mail.message_id,
            ))
            return
        run_result = self.chain_runner.run(chain, params, max_steps=self.settings.max_chain_steps)
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: цепочка '{name}' {'OK' if run_result.success else 'ОШИБКА'}",
            body=run_result.report(),
            in_reply_to=mail.message_id,
        ))

    async def _handle_save_chain(self, mail: IncomingMail, name: str) -> None:
        """Saves pending chain by name to chains/ directory."""
        chain = self._pending_chains.get(name)
        if not chain:
            # Попытка найти по частичному совпадению
            for key in self._pending_chains:
                if name in key:
                    chain = self._pending_chains[key]
                    break
        if not chain:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: цепочка не найдена",
                body=(
                    f"Цепочка '{name}' не найдена в очереди.\n"
                    "Сначала выполните: цепочка: <задача>"
                ),
                in_reply_to=mail.message_id,
            ))
            return
        chain.name = name
        path = self.chain_runner.save_chain(chain)
        del self._pending_chains[list(self._pending_chains.keys())[
            list(self._pending_chains.values()).index(chain)
        ]]
        steps_desc = "\n".join(f"  {i+1}. {s.skill}" for i, s in enumerate(chain.steps))
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: цепочка '{name}' сохранена",
            body=(
                f"Цепочка '{name}' сохранена в {path.name}\n\n"
                f"Шаги:\n{steps_desc}\n\n"
                f"Запуск: выполни цепочку {name}: url=..."
            ),
            in_reply_to=mail.message_id,
        ))

    def _handle_list_skills(self, mail: IncomingMail) -> None:
        """Отправляет оператору список закреплённых и pending навыков."""
        report = self.skill_builder.skills_report()
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: список навыков",
            body=report,
            in_reply_to=mail.message_id,
        ))

    async def _handle_confirm_skill(self, mail: IncomingMail, skill_name: str) -> None:
        """Перемещает навык из pending/ в skills/."""
        ok = self.skill_builder.confirm(skill_name)
        pending = self.skill_builder.list_pending()
        if ok:
            body = (
                f"Навык '{skill_name}' закреплён в data/skills/.\n"
                f"Ожидают подтверждения: {', '.join(pending) or 'нет'}"
            )
            subject = f"APIAt: навык '{skill_name}' закреплён"
        else:
            body = (
                f"Навык '{skill_name}' не найден в pending/.\n"
                f"Доступные навыки для подтверждения: {', '.join(pending) or 'нет'}"
            )
            subject = f"APIAt: навык '{skill_name}' не найден"
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=subject,
            body=body,
            in_reply_to=mail.message_id,
        ))

    async def _handle_llm_status(self, mail: IncomingMail) -> None:
        """Показывает текущие LLM-провайдеры и diff с бэкапом."""
        corrector = SelfCorrector(self.settings.db_path.parent.parent / ".env")
        providers = self.settings.llm_providers()
        router = self.parser.router
        lines = ["Текущие LLM-провайдеры:"]
        for p in providers:
            status = "⚠ cooldown" if router._is_cooling_down(p.name) else "✓ доступен"
            lines.append(f"  {p.name} ({p.provider_type}): {p.model_name} — {status}")
        if corrector.has_backup():
            lines.append("\nОтличия от .env.bak:")
            lines.extend(corrector.diff())
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: LLM статус",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

    async def _handle_llm_config_command(self, mail: IncomingMail) -> None:
        """Переключение LLM: swap primary↔fallback или новые параметры.

        Формат команды:
            переключи llm                          — поменять местами primary и fallback
            переключи llm                          — + новые параметры (см. ниже)
            LLM_BASE_URL=https://...
            LLM_API_KEY=...
            LLM_MODEL_NAME=...
        """
        env_path = self.settings.db_path.parent.parent / ".env"
        corrector = SelfCorrector(env_path)
        updates = _parse_env_updates(mail.body)

        if not updates:
            # Нет параметров — swap primary ↔ fallback
            try:
                applied = corrector.swap_providers()
            except Exception as exc:  # noqa: BLE001
                self._safe_send(OutgoingMail(
                    to=mail.sender,
                    subject="APIAt: LLM swap — ошибка",
                    body=f"Не удалось поменять провайдеры местами: {exc}",
                    in_reply_to=mail.message_id,
                ))
                return
            # Проверяем новые настройки
            get_settings.cache_clear()
            new_settings = get_settings()
            try:
                await _probe_llm(new_settings)
                msg = "Провайдеры поменяли местами:\n" + "\n".join(applied)
                self.settings = new_settings
                self._init_llm_components()
                self._safe_send(OutgoingMail(
                    to=mail.sender,
                    subject="APIAt: LLM swap — OK",
                    body=msg,
                    in_reply_to=mail.message_id,
                ))
            except Exception as exc:  # noqa: BLE001
                # Откат swap
                corrector.rollback()
                get_settings.cache_clear()
                self._safe_send(OutgoingMail(
                    to=mail.sender,
                    subject="APIAt: LLM swap — откат",
                    body=f"Новые настройки не прошли проверку — откат.\nОшибка: {exc}\n\nПопытка swap:\n" + "\n".join(applied),
                    in_reply_to=mail.message_id,
                ))
            return

        # Есть параметры — применяем как новые значения
        success, msg = await try_apply_and_verify(updates, env_path=env_path)
        if success:
            get_settings.cache_clear()
            self.settings = get_settings()
            self._init_llm_components()
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: LLM {'обновлён' if success else 'откат'}",
            body=msg,
            in_reply_to=mail.message_id,
        ))

    async def _handle_analyze_mails(self, mail: IncomingMail) -> None:
        """LLM анализирует кольцевой лог писем и генерирует рекомендации."""
        ring = self.mail_ring.get_ring()
        if not ring:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: анализ писем",
                body="Лог писем пуст — нет данных для анализа. Начните пользоваться агентом, затем запросите анализ снова.",
                in_reply_to=mail.message_id,
            ))
            return

        # Формируем сводку для LLM
        entries_text = "\n".join(
            f"[{e['ts'][:16]}] {e['task_type'] or '?'} | {e['status'] or '?'} | "
            f"от: {e['sender']} | тема: {e['subject']} | "
            f"превью: {e['body_preview'][:100]}"
            for e in ring
        )
        prompt = (
            "Ты — аналитик персонального AI-агента APIAt.\n\n"
            "## Контекст и роль сервиса\n"
            "APIAt — персональный интернет-агент, работающий исключительно через канал "
            "электронной почты (IMAP/SMTP). Его ключевая роль: обход жёстких сетевых "
            "блокировок и цензуры — пользователь находится в зоне ограниченного доступа "
            "к интернету (корпоративные фильтры, государственные блокировки, VPN-запреты) "
            "и общается с агентом только через email, который проходит эти барьеры. "
            "Агент действует как 'живой человек по ту сторону стены': ищет информацию, "
            "скачивает файлы, смотрит видео, читает заблокированные сайты — и возвращает "
            "результат письмом. Переписка может анализироваться третьими лицами, поэтому "
            "пользователи склонны использовать эвфемизмы и косвенные формулировки.\n\n"
            "## Возможности агента сейчас\n"
            "Поиск (Google News RSS / LLM-дайджест), скачивание файлов, YouTube (видео/аудио/"
            "субтитры/обложка/метаданные), браузер Chromium с сохранением сессий, "
            "архивирование вложений, самообучение через Docker-sandbox (создание навыков), "
            "цепочки навыков, управление whitelist, адаптивный polling.\n\n"
            f"## Лог последних {len(ring)} запросов\n"
            f"{entries_text}\n\n"
            "## Задача анализа\n"
            "1. Выяви повторяющиеся паттерны — что чаще всего нужно пользователям "
            "с учётом контекста обхода блокировок.\n"
            "2. Определи скрытые потребности: какие запросы могут быть эвфемизмами "
            "реальных задач (например, 'найди новости' может значить 'дай мне то, "
            "что заблокировано у меня').\n"
            "3. Предложи конкретные новые навыки (skills) для самообучения агента — "
            "с коротким описанием что делает каждый навык.\n"
            "4. Укажи провалившиеся задачи (FAILED) и их вероятные причины с учётом "
            "специфики VPS-сервера (блокировки по IP, rate-limit YouTube/Google и т.д.).\n"
            "5. Дай приоритетный список улучшений сервиса.\n"
            "Ответ — структурированный текст на русском, конкретный, без воды."
        )

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: анализ писем — запущен",
            body=f"Анализирую {len(ring)} последних писем, подождите...",
            in_reply_to=mail.message_id,
        ))

        try:
            result = await self.parser.router.run_agent(
                lambda model: __import__("pydantic_ai", fromlist=["Agent"]).Agent(
                    model=model, output_type=str,
                    system_prompt=(
                        "Ты — аналитик персонального AI-агента APIAt, который помогает людям "
                        "обходить интернет-блокировки через канал электронной почты. "
                        "Пользователи пишут письма агенту из зон с ограниченным доступом к сети. "
                        "Твоя задача — анализировать запросы, выявлять паттерны и скрытые потребности, "
                        "предлагать улучшения. Отвечай только на русском, структурированно и конкретно."
                    )
                ),
                prompt,
            )
            rec_text = (
                result.output if hasattr(result, "output") else
                result.data if hasattr(result, "data") else
                str(result)
            )
        except Exception as exc:  # noqa: BLE001
            rec_text = f"Ошибка LLM-анализа: {exc}"

        self.mail_ring.save_recommendation(rec_text)
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: рекомендации по анализу писем",
            body=rec_text,
            in_reply_to=mail.message_id,
        ))

    def _handle_show_recommendations(self, mail: IncomingMail) -> None:
        """Показывает сохранённые рекомендации."""
        body = self.mail_ring.format_recommendations()
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: рекомендации",
            body=body,
            in_reply_to=mail.message_id,
        ))

    def _effective_whitelist(self) -> list[str]:
        """Объединяет whitelist из .env и динамические записи из БД, учитывает исключения."""
        base = [a.lower() for a in self.settings.whitelist]
        extra = self.storage.whitelist_get()
        excluded_set = self.storage.whitelist_get_excluded()
        seen: dict[str, None] = {}
        for addr in base + extra:
            if addr not in excluded_set:
                seen[addr] = None
        return list(seen)

    def _handle_whitelist_add(self, mail: IncomingMail, email: str) -> None:
        from .gateway.security import extract_address
        addr = extract_address(email) or email.strip().lower()
        if not addr or "@" not in addr:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: whitelist — ОШИБКА",
                body=f"Некорректный адрес: {email!r}",
                in_reply_to=mail.message_id,
            ))
            return
        self.storage.whitelist_add(addr)
        wl = self._effective_whitelist()
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: whitelist обновлён",
            body=f"Добавлен: {addr}\n\nТекущий whitelist:\n" + "\n".join(f"  • {a}" for a in sorted(wl)),
            in_reply_to=mail.message_id,
        ))

    def _handle_whitelist_remove(self, mail: IncomingMail, email: str) -> None:
        from .gateway.security import extract_address
        addr = extract_address(email) or email.strip().lower()
        sender_addr = extract_address(mail.sender)
        wl = self._effective_whitelist()

        if addr not in wl:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: whitelist — адрес не найден",
                body=f"{addr!r} не в whitelist.\n\nТекущий whitelist:\n" + "\n".join(f"  • {a}" for a in sorted(wl)),
                in_reply_to=mail.message_id,
            ))
            return

        # Защита: нельзя удалить свой адрес если он последний
        remaining = [a for a in wl if a != addr]
        if addr == sender_addr and not remaining:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: whitelist — ЗАПРЕЩЕНО",
                body=f"Нельзя удалить {addr!r} — это последний адрес в whitelist и он ваш.\nДобавьте другой адрес перед удалением.",
                in_reply_to=mail.message_id,
            ))
            return

        self.storage.whitelist_remove(addr)
        # Удаляем также из .env-части через БД-пометку (если был в .env — запишем исключение)
        self.storage.set_setting(f"wl_excluded:{addr}", "1")
        wl_new = self._effective_whitelist()
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: whitelist обновлён",
            body=f"Удалён: {addr}\n\nТекущий whitelist:\n" + "\n".join(f"  • {a}" for a in sorted(wl_new)),
            in_reply_to=mail.message_id,
        ))

    def _handle_whitelist_list(self, mail: IncomingMail) -> None:
        wl = self._effective_whitelist()
        body = "Текущий whitelist:\n" + "\n".join(f"  • {a}" for a in sorted(wl)) if wl else "Whitelist пуст."
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: whitelist",
            body=body,
            in_reply_to=mail.message_id,
        ))

    def _handle_help(self, mail: IncomingMail) -> None:
        """Отправляет список всех команд и навыков."""
        skills = self.skill_builder.list_skills()
        pending = self.skill_builder.list_pending()

        lines = [
            "=== APIAt — справка ===",
            "",
            "## Задачи (свободный текст, LLM понимает запрос)",
            "  поиск / новости   — информация через Google News RSS или LLM-дайджест",
            "  скачай            — файл по URL (лимит 100 MB)",
            "  youtube           — видео/аудио/субтитры/обложка/метаданные канала",
            "                      качество: 144/240/360/480(по умолч.)/720/1080p",
            "                      файл хранится 2 ч, субтитры — мгновенно (без 429)",
            "  браузер           — открыть страницу, авторизоваться, извлечь текст",
            "  файл/архив        — упаковать вложения в zip",
            "",
            "## Команды оператора",
            "  обнови код              — git pull + перезапуск сервиса",
            "  обнови среду            — пересборка Docker sandbox-образа",
            "  список llm              — статус LLM-провайдеров",
            "  переключи llm           — поменять местами primary и fallback",
            "  переключи llm + KEY=..  — записать новые параметры LLM",
            "  лимит                  — показать/установить лимиты отправки",
            "  логи сервера           — анализ системных логов (journalctl)",
            "  логи приложения        — анализ ошибок приложения через LLM",
            "",
            "## Самообучение и навыки",
            "  самообучись: <описание>         — создать навык: LLM → ревью → sandbox",
            "  закрепи навык <имя>             — перенести из pending в skills",
            "  список навыков                  — показать все навыки",
            "  запусти навык <имя>             — явный запуск по имени",
            "  <имя навыка>                    — нечёткое совпадение (напр. 'статус сервера'→server_status)",
            "",
            "## Цепочки навыков",
            "  цепочка: <задача>               — LLM строит план из навыков",
            "  сохрани цепочку <имя>           — сохранить последнюю цепочку",
            "  выполни цепочку <имя>: key=val — запустить по сохранённому плану",
            "",
            "## Анализ переписки",
            "  анализ писем   — LLM анализирует последние 50 писем:",
            "                   паттерны, скрытые потребности, рекомендации по навыкам",
            "                   (лог сбрасывается при перезапуске, рекомендации — нет)",
            "  рекомендации / статус агента — последние 5 рекомендаций",
            "",
            "## Управление доступом",
            "  покажи whitelist                     — список разрешённых адресов",
            "  добавь в whitelist user@example.com  — добавить адрес",
            "  убери из whitelist user@example.com  — удалить адрес",
            "",
            "## Браузер и скриншоты",
            "  браузер / открой https://...     — извлечь текст страницы",
            "  скриншот https://...             — снимок страницы во вложении",
            "  авторизуйся: url=https://... логин=user пароль=pass",
            "    — Chromium входит на сайт, сессия сохраняется в БД",
            "  многошаговая авторизация: url=https://...",
            "    fill <селектор> <значение>",
            "    click <селектор>",
            "    wait <мс>",
            "    goto <url>",
            "    — поддерживает подстановки {login} и {password}",
            "",
            "## Помощь",
            "  помощь / help  — эта справка",
        ]

        if skills:
            lines += ["", "## Закреплённые навыки"]
            for s in skills:
                lines.append(f"  • {s}")
        if pending:
            lines += ["", "## Ожидают подтверждения"]
            for s in pending:
                lines.append(f"  • {s}  →  закрепи навык {s}")

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: справка по командам",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

    async def _handle_browser_auth(self, mail: IncomingMail, params_str: str) -> None:
        """Авторизация на сайте через Playwright. Сессия сохраняется в SQLite.

        Формат команды:
            авторизуйся: url=https://example.com логин=user пароль=pass
            авторизуйся: url=https://example.com логин=user пароль=pass селектор_логина=#email селектор_пароля=#password кнопка=button[type=submit]
        """
        params = _parse_inline_params(params_str)
        url = params.get("url")
        login = params.get("логин") or params.get("login") or params.get("user")
        password = params.get("пароль") or params.get("password") or params.get("pass")

        if not url:
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: авторизация — ОШИБКА",
                body="Укажите url=... в команде.\nПример:\n  авторизуйся: url=https://example.com логин=user пароль=pass",
                in_reply_to=mail.message_id,
            ))
            return

        sel_login = params.get("селектор_логина") or params.get("login_selector") or "input[type=email], input[type=text], input[name*=login], input[name*=user], input[name*=email]"
        sel_pass = params.get("селектор_пароля") or params.get("pass_selector") or "input[type=password]"
        sel_btn = params.get("кнопка") or params.get("button") or "button[type=submit], input[type=submit]"

        try:
            result_msg = await self._do_browser_auth(url, login, password, sel_login, sel_pass, sel_btn)
        except Exception as exc:  # noqa: BLE001
            result_msg = f"Ошибка: {exc}"

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: браузерная авторизация",
            body=result_msg,
            in_reply_to=mail.message_id,
        ))

    async def _do_browser_auth(
        self, url: str, login: str | None, password: str | None,
        sel_login: str, sel_pass: str, sel_btn: str,
    ) -> str:
        from urllib.parse import urlparse
        from playwright.async_api import async_playwright

        domain = urlparse(url).netloc
        saved_state = self.storage.load_browser_session(domain)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx_opts = {"storage_state": saved_state} if saved_state else {}
                context = await browser.new_context(**ctx_opts)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                lines = [f"Открыта страница: {url}"]

                if login and password:
                    try:
                        await page.locator(sel_login).first.fill(login, timeout=5_000)
                        await page.locator(sel_pass).first.fill(password, timeout=5_000)
                        await page.locator(sel_btn).first.click(timeout=5_000)
                        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                        lines.append("Форма отправлена")
                    except Exception as e:  # noqa: BLE001
                        lines.append(f"Не удалось заполнить форму: {e}")
                else:
                    lines.append("Логин/пароль не указаны — сессия открыта без авторизации")

                title = await page.title()
                lines.append(f"Заголовок страницы: {title}")

                state = await context.storage_state()
                self.storage.save_browser_session(domain, state)
                if state.get("cookies"):
                    self.storage.save_cookies(domain, state["cookies"])
                lines.append(f"Сессия сохранена для домена: {domain}")
                lines.append("Последующие запросы к этому домену будут использовать эту сессию автоматически.")
                return "\n".join(lines)
            finally:
                await browser.close()

    async def _handle_browser_multi_auth(self, mail: IncomingMail, params_str: str) -> None:
        """Многошаговая авторизация через Playwright по списку шагов."""
        params = _parse_inline_params(params_str)
        url = params.get("url")
        login = params.get("логин") or params.get("login") or params.get("user")
        password = params.get("пароль") or params.get("password") or params.get("pass")

        steps = _parse_auth_steps(params_str, login, password)
        if not url:
            body = "Укажите url=... в команде.\nПример:\nмногошаговая авторизация: url=https://site.com\nfill #email user@example.com\nclick button.next\nfill #password pass\nclick button[type=submit]"
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: многошаговая авторизация — ОШИБКА",
                body=body,
                in_reply_to=mail.message_id,
            ))
            return

        try:
            result_msg = await self._do_browser_multi_auth(url, steps)
        except Exception as exc:  # noqa: BLE001
            result_msg = f"Ошибка: {exc}"

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject="APIAt: многошаговая авторизация",
            body=result_msg,
            in_reply_to=mail.message_id,
        ))

    async def _handle_screenshot(self, mail: IncomingMail, target: str) -> None:
        """Делает скриншот страницы через browser workflow и отправляет результат."""
        from .models.tasks import BrowserTask
        url = target.split()[0]
        task = BrowserTask(url=url, instruction="screenshot", screenshot=True)
        task.source_email = mail.sender
        task.message_id = mail.message_id
        self.storage.save_task(task.task_id, task.type.value, "PARSED", task.model_dump(mode="json"))
        try:
            state = await self.engine.run(task)
        except Exception as exc:  # noqa: BLE001
            self._reply_error(mail, f"Ошибка скриншота: {exc}", task_name="browser")
            return
        status = state.get("status", "FAILED")
        result = state.get("result", {})
        self.storage.save_result(task.task_id, summary=result.get("summary", ""), data=result.get("data", {}))
        self._reply_result(mail, "browser", status, result, elapsed=0)

    def _reply_result(
        self, mail: IncomingMail, task_name: str, status: str, result: dict, elapsed: float
    ) -> None:
        """Формирует и отправляет ответ с результатом.

        Если тело > zip_threshold — сохраняет в pending_sends и запрашивает подтверждение
        у оператора (отправить частями / zip / zip N частей).
        """
        from .models.email import Attachment

        attachments = [Attachment(**a) for a in result.get("attachments", [])]
        summary = result.get("summary") or result.get("error", "")
        body = (
            f"Task: {task_name}\n"
            f"Status: {status}\n\n"
            f"{summary}\n\n"
            f"Execution Time: {elapsed:.1f} sec"
        )

        # Gate: если результат большой — запрашиваем подтверждение
        if len(summary) > self.settings.zip_threshold:
            self._request_send_confirmation(
                mail, task_name, status, summary, attachments, elapsed
            )
            return

        self._safe_send(
            OutgoingMail(
                to=mail.sender,
                subject=f"APIAt: {task_name} [{status}]",
                body=body,
                attachments=attachments,
                in_reply_to=mail.message_id,
            )
        )

    def _request_send_confirmation(
        self,
        mail: IncomingMail,
        task_name: str,
        status: str,
        body: str,
        attachments: list,
        elapsed: float,
    ) -> None:
        """Сохраняет результат в pending_sends и отправляет вопрос оператору."""
        import json as _json
        import uuid as _uuid
        from .tools.email_tool import estimate_email_count
        from .utils.zip_util import estimate_zip_size

        token = _uuid.uuid4().hex[:12]
        att_json = _json.dumps(
            [{"filename": a.filename, "path": a.path, "content_type": a.content_type}
             for a in attachments],
            ensure_ascii=False,
        )
        self.storage.save_pending_send(
            token=token,
            sender=mail.sender,
            subject=f"APIAt: {task_name} [{status}]",
            body=body,
            attachments=att_json,
            task_name=task_name,
            status=status,
            elapsed=elapsed,
            message_id=mail.message_id,
        )

        email_count = estimate_email_count(len(body), self.settings.body_limit)
        zip_size = estimate_zip_size(body)
        zip_mb = zip_size / (1024 * 1024)
        limit_mb = self.settings.attachment_limit_mb

        lines = [
            f"Результат: {len(body):,} символов",
            f"Если отправить текстом: {email_count} писем (лимит {self.settings.body_limit:,} симв.)",
            f"Если сжать в zip: ~{zip_mb:.1f} MB (лимит вложения {limit_mb} MB)",
        ]
        if zip_mb > limit_mb:
            parts = int(zip_mb / limit_mb) + 1
            lines.append(f"Zip превысит лимит — потребуется разбить на ~{parts} частей")
        lines.append("")
        lines.append("Ответьте одним из вариантов:")
        lines.append("  да       — отправить текстом (частями)")
        lines.append("  zip      — сжать в zip и отправить одним письмом")
        lines.append("  zip 3    — сжать в zip и разбить на 3 части")

        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: {task_name} — подтверждение отправки",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

    async def _handle_send_confirmation(self, mail: IncomingMail, match: re.Match) -> None:
        """Обрабатывает ответ оператора на запрос подтверждения отправки."""
        import json as _json
        from .models.email import Attachment
        from .utils.zip_util import zip_text, split_zip

        pending = self.storage.load_pending_send_by_sender(mail.sender)
        if not pending:
            self._reply(mail, "APIAt: нет отложенных отправок",
                        "Для вас нет ожидающих подтверждения результатов.")
            return

        body = pending["body"]
        subject = pending["subject"]
        task_name = pending["task_name"]
        elapsed = pending["elapsed"] or 0.0
        msg_id = pending["message_id"] or ""
        att_data = _json.loads(pending["attachments"]) if pending["attachments"] else []
        attachments = [Attachment(**a) for a in att_data]

        choice = match.group(1).lower().strip()
        zip_parts = match.group(2)  # число частей для "zip N"

        if choice in ("да", "yes"):
            # Отправить текстом частями (EmailSender сам разобьёт)
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject=subject,
                body=body,
                attachments=attachments,
                in_reply_to=msg_id,
            ))
            self.storage.delete_pending_send(pending["token"])
            return

        if choice.startswith("zip"):
            # Сжимаем в zip
            zip_path = zip_text(body, filename=f"{task_name}.txt",
                                zip_name=f"{task_name}.zip")
            if zip_parts and int(zip_parts) > 1:
                # Разбиваем на N частей
                parts = int(zip_parts)
                split_paths = split_zip(zip_path, parts)
                for idx, part_path in enumerate(split_paths, 1):
                    self._safe_send(OutgoingMail(
                        to=mail.sender,
                        subject=f"{subject} [zip part {idx}/{parts}]",
                        body=f"Часть {idx} из {parts}. Соберите все части и распакуйте.",
                        attachments=[Attachment(
                            filename=part_path.name,
                            content_type="application/octet-stream",
                            path=str(part_path),
                        )],
                        in_reply_to=msg_id,
                    ))
            else:
                # Одним письмом с zip-вложением
                self._safe_send(OutgoingMail(
                    to=mail.sender,
                    subject=subject,
                    body=f"Результат сжат в zip: {task_name}.zip",
                    attachments=[Attachment(
                        filename=zip_path.name,
                        content_type="application/zip",
                        path=str(zip_path),
                    )],
                    in_reply_to=msg_id,
                ))
            self.storage.delete_pending_send(pending["token"])
            return

    async def _handle_server_logs(self, mail: IncomingMail) -> None:
        """Собирает логи journalctl -u apiat и отправляет в LLM для анализа."""
        try:
            r = subprocess.run(
                ["journalctl", "-u", "apiat", "--no-pager", "-n", "200",
                 "--since", "6h ago"],
                capture_output=True, text=True, timeout=15,
            )
            raw_logs = r.stdout or r.stderr or "journalctl недоступен"
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raw_logs = f"Не удалось получить логи: {exc}"

        if len(raw_logs) > 30_000:
            raw_logs = raw_logs[-30_000:]

        prompt = (
            "Ты системный администратор. Проанализируй логи сервиса apiat "
            "(systemd/journald). Найди проблемы, ошибки, предупреждения. "
            "Предложи варианты исправлений. Ответь на русском, кратко.\n\n"
            f"## Логи (последние строки)\n```\n{raw_logs}\n```"
        )
        try:
            analysis = await self.parser.router.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            analysis = f"LLM-анализ недоступен: {exc}\n\n## Сырые логи:\n{raw_logs[:5000]}"

        self._reply(mail, "APIAt: анализ логов сервера", analysis)

    async def _handle_app_logs(self, mail: IncomingMail) -> None:
        """Фильтрует логи приложения (ERROR/Exception/Traceback) и анализирует через LLM."""
        try:
            r = subprocess.run(
                ["journalctl", "-u", "apiat", "--no-pager", "-n", "500",
                 "--since", "6h ago"],
                capture_output=True, text=True, timeout=15,
            )
            raw_logs = r.stdout or r.stderr or ""
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raw_logs = f"Не удалось получить логи: {exc}"

        # Фильтруем только значимые строки приложения
        significant: list[str] = []
        for line in raw_logs.splitlines():
            if re.search(
                r"\[ERROR\]|\[WARNING\]|Exception|Traceback|Error:|"
                r"FAILED|LlmAllProvidersFailed|IMAP|SMTP|отклонено|"
                r"не удалось|ошибка|превышен",
                line, re.IGNORECASE,
            ):
                significant.append(line)

        if not significant:
            self._reply(mail, "APIAt: анализ логов приложения",
                        "Ошибок и предупреждений в логах за последние 6 часов не найдено.")
            return

        filtered = "\n".join(significant[-200:])
        if len(filtered) > 30_000:
            filtered = filtered[-30_000:]

        prompt = (
            "Ты DevOps-инженер. Проанализируй логи приложения APIAt "
            "(Python-агент, IMAP/SMTP, LLM-роутер). Найди причины ошибок, "
            "предложи конкретные исправления. Учитывай архитектуру: "
            "email → IMAP → LLM-парсер → workflow → SMTP-ответ. "
            "Ответь на русском, структурированно: проблемы → причины → исправления.\n\n"
            f"## Отфильтрованные логи (ERROR/WARNING/Exception)\n```\n{filtered}\n```"
        )
        try:
            analysis = await self.parser.router.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            analysis = f"LLM-анализ недоступен: {exc}\n\n## Найденные ошибки:\n{filtered[:5000]}"

        self._reply(mail, "APIAt: анализ логов приложения", analysis)

    def _handle_limit_command(self, mail: IncomingMail) -> None:
        """Показывает или устанавливает лимиты отправки.

        Формат:
            лимит                    — показать текущие лимиты
            лиммит body_limit=50000  — установить body_limit
            лимит attachment_limit_mb=15  — установить лимит вложения
            лимит zip_threshold=80000     — установить порог zip
        """
        allowed = {"body_limit", "attachment_limit_mb", "zip_threshold", "max_chain_steps"}
        updates: dict[str, str] = {}
        for line in mail.body.splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                key = key.strip().lower()
                if key in allowed and value.strip():
                    updates[key] = value.strip()

        if not updates:
            # Показать текущие лимиты
            lines = [
                "Текущие лимиты отправки:",
                f"  body_limit         = {self.settings.body_limit:,} символов",
                f"  attachment_limit_mb = {self.settings.attachment_limit_mb} MB",
                f"  zip_threshold      = {self.settings.zip_threshold:,} символов",
                f"  max_chain_steps    = {self.settings.max_chain_steps}",
                "",
                "Для изменения отправьте:",
                "  лимит body_limit=50000",
                "  лимит attachment_limit_mb=15",
                "  лимит zip_threshold=80000",
                "  лимит max_chain_steps=15",
            ]
            self._reply(mail, "APIAt: лимиты", "\n".join(lines))
            return

        # Применяем изменения через БД settings
        applied: list[str] = []
        for key, value in updates.items():
            try:
                if key in ("body_limit", "zip_threshold", "max_chain_steps"):
                    int(value)
                elif key == "attachment_limit_mb":
                    float(value)
            except ValueError:
                applied.append(f"  {key}: НЕВЕРНОЕ ЗНАЧЕНИЕ '{value}'")
                continue
            self.storage.set_setting(f"limit:{key}", value)
            applied.append(f"  {key} = {value}")

        # Применяем к текущему экземпляру settings
        for key, value in updates.items():
            try:
                if key == "body_limit":
                    self.settings.body_limit = int(value)
                elif key == "attachment_limit_mb":
                    self.settings.attachment_limit_mb = float(value)
                elif key == "zip_threshold":
                    self.settings.zip_threshold = int(value)
                elif key == "max_chain_steps":
                    self.settings.max_chain_steps = int(value)
            except (ValueError, TypeError):
                pass

        self._reply(mail, "APIAt: лимиты обновлены", "\n".join(applied))

    def _format_thread_context(self, thread: list[dict]) -> str:
        """Формирует текст с предыдущими письмами для LLM."""
        if not thread:
            return ""
        lines = ["=== Контекст переписки ==="]
        for m in thread:
            direction = "Пользователь" if m["direction"] == "in" else "Агент"
            lines.append(f"[{direction}] {m['subject']}")
            lines.append(m["body"][:800])
            lines.append("")
        return "\n".join(lines)

    def _match_skill(self, text: str) -> str:
        """Точное/частичное совпадение имени навыка в тексте (быстро, без LLM)."""
        skills = self.skill_builder.list_confirmed()
        if not skills:
            return ""
        normalized = re.sub(r"[^\w]", "_", text.strip().lower()).strip("_")
        for skill in skills:
            if skill in normalized or normalized in skill:
                return skill
        return ""

    async def _handle_run_skill(self, mail: IncomingMail, skill_name: str) -> None:
        """Запускает закреплённый навык в Docker sandbox и отправляет результат."""
        from .skills.sandbox import DockerSandbox, SkillConfig
        skill_path = self.settings.data_dir / "skills" / f"{skill_name}.py"
        if not skill_path.exists():
            self._reply(mail, f"APIAt: навык '{skill_name}' не найден",
                        f"Файл навыка не найден: {skill_path}")
            return
        code = skill_path.read_text(encoding="utf-8")
        cfg = SkillConfig.from_code(code)
        sandbox = DockerSandbox(data_dir=self.settings.data_dir)
        started = time.monotonic()
        result = sandbox.run(code, cfg)
        elapsed = round(time.monotonic() - started, 1)
        if result.success:
            body = f"Навык: {skill_name}\nВремя: {elapsed} сек\n\n{result.stdout}"
            subject = f"APIAt: {skill_name} [OK]"
        else:
            body = f"Навык: {skill_name}\nВремя: {elapsed} сек\n\nОшибка:\n{result.stderr}"
            subject = f"APIAt: {skill_name} [FAILED]"
        self._reply(mail, subject, body)
        self.mail_ring.push(mail, task_type=f"skill:{skill_name}",
                            status="COMPLETED" if result.success else "FAILED",
                            secret_token=self.settings.secret_token)

    def _reply(self, mail: IncomingMail, subject: str, body: str,
               attachments: list | None = None) -> None:
        """Отправить ответное письмо с правильными thread-заголовками."""
        from email.utils import make_msgid
        # References должен включать всю цепочку: prev refs + in_reply_to входящего письма
        refs = " ".join(
            m for m in (mail.references, mail.in_reply_to) if m.strip()
        )
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=subject,
            body=body,
            attachments=attachments or [],
            message_id=make_msgid(domain="apiat.local"),
            in_reply_to=mail.message_id,
            references=refs,
        ))

    def _reply_error(self, mail: IncomingMail, message: str, task_name: str = "unknown") -> None:
        self._reply(
            mail,
            subject=f"APIAt: {task_name} [FAILED]",
            body=f"Task: {task_name}\nStatus: FAILED\n\n{message}",
        )

    def _safe_send(self, mail: OutgoingMail, reply_to: IncomingMail | None = None) -> None:
        src = reply_to or self._current_mail
        if src and mail.in_reply_to and not mail.references:
            mail = mail.model_copy(update={"references": " ".join(
                m for m in (src.references, src.in_reply_to) if m.strip()
            )})
        try:
            self.sender.send(mail)
            # Сохраняем исходящее письмо для контекста треда
            self.storage.save_mail_thread(
                message_id=mail.message_id or mail.in_reply_to or f"out-{hash(mail.to + mail.subject)}",
                sender=mail.to,
                subject=mail.subject,
                body=mail.body,
                refs=mail.references,
                direction="out",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить ответ на %s", mail.to)

    async def run_once(self) -> int:
        """Один проход: забрать письма и обработать. Возвращает число писем."""
        mails = self.imap.fetch_unseen()
        logger.info("Получено новых писем: %d", len(mails))
        for mail in mails:
            await self.process_mail(mail)
        return len(mails)


def _parse_auth_steps(text: str, login: str | None, password: str | None) -> list[tuple[str, str, str]]:
    """Парсит список шагов для многошаговой авторизации.

    Формат строки:
      fill <selector> <value>
      click <selector>
      wait <milliseconds>
      goto <url>

    Поддерживает подстановки {login} и {password}.
    """
    steps: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Пропускаем строки с параметрами key=value
        if "=" in line and not line.startswith(("fill", "click", "wait", "goto")):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            continue
        action = parts[0].lower()
        selector = parts[1]
        value = parts[2] if len(parts) > 2 else ""
        if login:
            value = value.replace("{login}", login)
        if password:
            value = value.replace("{password}", password)
        steps.append((action, selector, value))
    return steps


async def _do_browser_multi_auth(url: str, steps: list[tuple[str, str, str]]) -> str:
    """Выполняет многошаговую авторизацию в Playwright."""
    from urllib.parse import urlparse
    from playwright.async_api import async_playwright

    domain = urlparse(url).netloc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            lines = [f"Открыта страница: {url}"]

            for action, selector, value in steps:
                try:
                    if action == "fill":
                        await page.locator(selector).first.fill(value, timeout=5_000)
                        lines.append(f"fill {selector}: OK")
                    elif action == "click":
                        await page.locator(selector).first.click(timeout=5_000)
                        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                        lines.append(f"click {selector}: OK")
                    elif action == "wait":
                        ms = int(value or "1000")
                        await page.wait_for_timeout(ms)
                        lines.append(f"wait {ms}ms: OK")
                    elif action == "goto":
                        await page.goto(selector, wait_until="domcontentloaded", timeout=30_000)
                        lines.append(f"goto {selector}: OK")
                    else:
                        lines.append(f"неизвестное действие: {action}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"{action} {selector}: {e}")
                    break

            title = await page.title()
            lines.append(f"Заголовок страницы: {title}")

            state = await context.storage_state()
            # domain берём от целевого url, если goto увёл куда-то ещё
            target_domain = urlparse(page.url).netloc or domain
            storage = Storage(get_settings().db_path)
            storage.save_browser_session(target_domain, state)
            if state.get("cookies"):
                storage.save_cookies(target_domain, state["cookies"])
            lines.append(f"Сессия сохранена для домена: {target_domain}")
            return "\n".join(lines)
        finally:
            await browser.close()


def _parse_inline_params(text: str) -> dict[str, str]:
    """Извлекает параметры вида key=value из строки.

    Поддерживает:
      - простые значения: key=value
      - URL: url=https://example.com/path?q=1
      - quoted: key="value with spaces"
    """
    result: dict[str, str] = {}
    # Сначала ищем quoted-значения, затем обычные (включая URL)
    for m in re.finditer(r'([\w\u0400-\u04ff]+)="([^"]*)"', text):
        result[m.group(1)] = m.group(2)
    # Не перезаписываем уже найденные quoted, ищем оставшиеся key=value
    for m in re.finditer(r'([\w\u0400-\u04ff]+)=([^\s"]+)', text):
        key = m.group(1)
        if key not in result:
            result[key] = m.group(2)
    return result


def _parse_env_updates(body: str) -> dict[str, str]:
    """Извлекает KEY=value из тела письма (только LLM-ключи)."""
    allowed = {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_NAME",
               "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL_NAME"}
    result: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            key = key.strip()
            if key in allowed and value.strip():
                result[key] = value.strip()
    return result
