#!/usr/bin/env python3
"""Run subscription ChatGPT Deep Research jobs through a persistent CLI contract."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

# Keep Deep Research isolated from the original review browser and its state.
os.environ.setdefault("INSANE_REVIEW_CDP_PORT", "9333")
os.environ.setdefault(
    "INSANE_REVIEW_PROFILE",
    os.environ.get(
        "INSANE_RESEARCH_PROFILE",
        str(Path.home() / ".insane-research" / "browser-profile"),
    ),
)
os.environ.setdefault(
    "INSANE_REVIEW_CONFIG",
    os.environ.get(
        "INSANE_RESEARCH_CONFIG",
        str(Path.home() / ".insane-research" / "config.json"),
    ),
)

import pack_and_ask as web


DEFAULT_OUT_DIR = Path.cwd() / ".insane-research"
DEFAULT_TARGET_MODEL = "GPT-5.6 Sol"
DEFAULT_TARGET_EFFORT = "Extra High"
DEEP_RESEARCH_TEXT = re.compile(
    r"deep\s*research|深入研究|深度研究|深度調查|심층\s*리서치|recherche\s+approfondie",
    re.IGNORECASE,
)
DEEP_RESEARCH_DONE_TEXT = re.compile(
    r"^[ \t]*(?:(?:deep\s+)?research\s+(?:is\s+)?(?:now\s+)?complete(?:d)?|"
    r"(?:深度(?:研究|調查)|研究)(?:已|已經|已经)?完成|리서치가\s+완료(?:되었습니다)?)[.!。！]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
DEEP_RESEARCH_CLARIFICATION_TEXT = re.compile(
    r"\bclarif(?:y|ication|ications)\b|\b(?:need|require)\s+(?:your\s+)?(?:answer|input)\b|"
    r"\bplease\s+(?:answer|confirm|choose|specify)\b|澄清|請(?:先)?(?:回答|確認|選擇|說明)|"
    r"需要(?:你|您)?(?:回答|確認|選擇|提供)",
    re.IGNORECASE,
)
DEEP_RESEARCH_MENU_OPENERS = [
    'button[data-testid="composer-plus-btn"]',
    'button[data-testid*="tools" i]',
    'button[aria-label*="tools" i]',
    'button[aria-label*="tool" i]',
    'button[aria-label*="add" i]',
]
DEEP_RESEARCH_CHOICES = [
    '[role="menuitem"]',
    '[role="menuitemradio"]',
    '[role="option"]',
    'button',
    'div.__menu-item',
]
MODEL_ITEM_SELECTORS = [
    '[role="menuitem"]',
    '[role="menuitemradio"]',
    '[role="option"]',
]
EFFORT_ALIASES = {
    "Extra High": (
        "extra high",
        "very high",
        "超高",
        "非常高",
        "非常高等",
        "매우 높음",
        "très élevé",
    ),
}
MODEL_ROW_LABELS = {"model", "模型", "모델", "modèle", "modelo", "モデル"}
EFFORT_ROW_LABELS = {
    "reasoning effort",
    "reasoning",
    "推理強度",
    "推理力度",
    "추론 단계",
    "추론 강도",
    "niveau de raisonnement",
    "推論強度",
}


class SentUnknownLocationError(RuntimeError):
    """The prompt may be submitted, but no durable conversation identity exists."""


def _is_wsl_without_display() -> bool:
    return (
        platform.system() == "Linux"
        and "microsoft" in platform.release().lower()
        and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )


def _windows_python() -> Path | None:
    configured = os.environ.get("INSANE_RESEARCH_WINDOWS_PYTHON")
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    executable = shutil.which("python.exe")
    if executable:
        return Path(executable)
    users_root = Path("/mnt/c/Users")
    if not users_root.is_dir():
        return None
    candidates = sorted(
        users_root.glob("*/AppData/Local/Microsoft/WindowsApps/python.exe")
    )
    return candidates[0] if candidates else None


def _wsl_to_windows_path(raw_path: str) -> str:
    resolved = Path(raw_path).expanduser().resolve().as_posix()
    configured_wslpath = os.environ.get("INSANE_RESEARCH_WSLPATH")
    wslpath = configured_wslpath if configured_wslpath is not None else shutil.which("wslpath")
    if wslpath:
        try:
            result = subprocess.run(
                [wslpath, "-w", resolved],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass

    distro = (
        os.environ.get("INSANE_RESEARCH_WSL_DISTRO")
        or os.environ.get("WSL_DISTRO_NAME")
    )
    if not distro:
        raise RuntimeError(
            "WSL path translation is unavailable and the current distro name is unknown"
        )
    match = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", resolved)
    if match:
        drive = match.group(1).upper()
        remainder = (match.group(2) or "").replace("/", "\\")
        return f"{drive}:\\{remainder}"
    remainder = resolved.lstrip("/").replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}\\{remainder}"


def _windows_python_usable(executable: Path) -> bool:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    version_text = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "Python " in version_text


def _wsl_mount_root() -> str:
    configured = os.environ.get("INSANE_RESEARCH_WSL_MOUNT_ROOT")
    if configured:
        return configured.rstrip("/") or "/"
    try:
        text = Path("/etc/wsl.conf").read_text(encoding="utf-8")
        match = re.search(r"(?im)^\s*root\s*=\s*([^#\r\n]+)", text)
        if match:
            return match.group(1).strip().rstrip("/") or "/"
    except OSError:
        pass
    return "/mnt"


def _windows_browser_arg(value: str | None) -> str | None:
    if value and (value.startswith("/") or value.startswith("~")):
        return _wsl_to_windows_path(value)
    return value


def _windows_argv(args: argparse.Namespace) -> list[str]:
    if args.command == "start":
        converted = [
            "start",
            "--prompt-file",
            _wsl_to_windows_path(args.prompt_file),
            "--out-dir",
            _wsl_to_windows_path(args.out_dir),
        ]
        if args.dry_run:
            converted.append("--dry-run")
        converted.extend(["--browser-driver", args.browser_driver])
        browser = _windows_browser_arg(args.browser)
        if browser:
            converted.extend(["--browser", browser])
        if args.json:
            converted.append("--json")
        return converted
    if args.command == "status":
        converted = ["status", _wsl_to_windows_path(args.run_dir)]
        if args.refresh:
            converted.append("--refresh")
        browser = _windows_browser_arg(args.browser)
        if browser:
            converted.extend(["--browser", browser])
        if args.json:
            converted.append("--json")
        return converted
    if args.command == "record":
        converted = [
            "record",
            _wsl_to_windows_path(args.run_dir),
            "--observation-file",
            _wsl_to_windows_path(args.observation_file),
        ]
        if args.json:
            converted.append("--json")
        return converted
    converted = ["fetch", _wsl_to_windows_path(args.run_dir)]
    if args.json:
        converted.append("--json")
    return converted


def _maybe_reexec_on_windows(args: argparse.Namespace) -> int | None:
    if os.environ.get("INSANE_RESEARCH_WINDOWS_REEXEC") == "1":
        return None
    if not _is_wsl_without_display():
        return None
    force_reexec = os.environ.get("INSANE_RESEARCH_FORCE_WINDOWS_REEXEC") == "1"
    live_browser_call = (
        (
            args.command == "start"
            and not args.dry_run
            and args.browser_driver == "cdp"
        )
        or (args.command == "status" and args.refresh)
    )
    if not force_reexec and not live_browser_call:
        return None
    windows_python = _windows_python()
    if windows_python is None or not _windows_python_usable(windows_python):
        print(
            "Deep Research live browser on WSL without DISPLAY requires "
            "Windows Python with Playwright; local fallback is disabled.",
            file=sys.stderr,
        )
        return 5

    child_env = os.environ.copy()
    child_env["INSANE_RESEARCH_WINDOWS_REEXEC"] = "1"
    child_env["PYTHONUTF8"] = "1"
    child_env["INSANE_RESEARCH_WSL_DISTRO"] = os.environ.get(
        "WSL_DISTRO_NAME", ""
    )
    child_env["INSANE_RESEARCH_WSL_MOUNT_ROOT"] = _wsl_mount_root()
    inherited_wslenv = child_env.get("WSLENV", "")
    forwarded = [
        "INSANE_RESEARCH_WINDOWS_REEXEC/u",
        "INSANE_RESEARCH_WSL_DISTRO/u",
        "INSANE_RESEARCH_WSL_MOUNT_ROOT/u",
        "INSANE_REVIEW_CDP_PORT/u",
        "INSANE_RESEARCH_PROFILE/p",
        "INSANE_RESEARCH_CONFIG/p",
        "PYTHONUTF8/u",
    ]
    child_env["WSLENV"] = ":".join(
        [part for part in [inherited_wslenv, *forwarded] if part]
    )
    try:
        command = [
            str(windows_python),
            _wsl_to_windows_path(str(Path(__file__).resolve())),
            *_windows_argv(args),
        ]
    except RuntimeError as exc:
        print(f"Deep Research Windows path translation failed: {exc}", file=sys.stderr)
        return 5
    try:
        before_runs: set[Path] = set()
        output_root = None
        if args.command == "start":
            output_root = Path(args.out_dir).expanduser().resolve()
            if output_root.is_dir():
                before_runs = set(output_root.iterdir())
        result = subprocess.run(
            command,
            env=child_env,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        print(f"Deep Research Windows Python failed to start: {exc}", file=sys.stderr)
        return 5
    hardened = False
    if args.command == "start" and args.json:
        for line in reversed(result.stdout.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_dir = payload.get("run_dir") if isinstance(payload, dict) else None
            if isinstance(run_dir, str):
                _harden_run_permissions(Path(run_dir).expanduser().resolve())
                hardened = True
            break
    if args.command == "start" and not hardened and output_root is not None:
        if output_root.is_dir():
            new_runs = [path for path in output_root.iterdir() if path not in before_runs]
            if len(new_runs) == 1:
                _harden_run_permissions(new_runs[0])
    elif args.command == "status":
        _harden_run_permissions(Path(args.run_dir).expanduser().resolve())
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def _display_path(path: Path) -> str:
    raw = str(path)
    if platform.system() != "Windows":
        return raw
    normalized = raw.replace("/", "\\")
    match = re.match(
        r"^\\\\(?:wsl\.localhost|wsl\$)\\[^\\]+\\(.*)$",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return "/" + match.group(1).replace("\\", "/")
    drive_match = re.match(r"^([a-zA-Z]):\\(.*)$", normalized)
    if drive_match:
        mount_root = os.environ.get("INSANE_RESEARCH_WSL_MOUNT_ROOT", "/mnt")
        drive = drive_match.group(1).lower()
        remainder = drive_match.group(2).replace("\\", "/")
        return f"{mount_root.rstrip('/')}/{drive}/{remainder}"
    return raw


def atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _harden_run_permissions(run_dir: Path) -> None:
    """Apply POSIX privacy modes after Windows writes through a WSL UNC path."""
    try:
        if not run_dir.is_dir() or run_dir.is_symlink():
            return
        run_dir.chmod(0o700)
        for path in run_dir.iterdir():
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
    except OSError:
        return


def create_run(
    prompt: str,
    output_root: Path,
    *,
    browser_driver: str = "cdp",
    target_model: str = DEFAULT_TARGET_MODEL,
    target_effort: str = DEFAULT_TARGET_EFFORT,
) -> tuple[Path, dict[str, object]]:
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().astimezone()

    while True:
        run_id = f"{created_at:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        run_dir = output_root / run_id
        try:
            run_dir.mkdir()
            run_dir.chmod(0o700)
            break
        except FileExistsError:
            continue

    state: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "CREATED",
        "runtime_os": platform.system(),
        "browser_driver": browser_driver,
        "target_model": target_model,
        "target_effort": target_effort,
        "cdp_port": web.CDP_PORT,
        "browser_profile": str(web.BROWSER_PROFILE_DIR),
        "browser_config": str(web.CONFIG_PATH),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "conversation_url": None,
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "report_file": None,
        "error": None,
    }
    atomic_write_text(run_dir / "request.md", prompt)
    atomic_write_text(
        run_dir / "state.json",
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )
    _harden_run_permissions(run_dir)
    return run_dir, state


def load_state(run_dir: Path) -> dict[str, object]:
    state_path = run_dir.expanduser().resolve() / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read run state: {state_path}: {exc}") from exc
    if state.get("schema_version") != 1 or not state.get("run_id") or not state.get("status"):
        raise ValueError(f"Invalid run state: {state_path}")
    return state


def save_state(run_dir: Path, state: dict[str, object]) -> None:
    state["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_write_text(
        run_dir / "state.json",
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )


def _node_text(node) -> str:
    try:
        return (node.inner_text() or "").strip()
    except Exception:
        return ""


def _deep_research_is_active(page) -> bool:
    selectors = [
        '[data-inline-selection-pill][data-id="plugin:connector_openai_deep_research"]',
        'button[aria-pressed="true"]',
        'button[aria-checked="true"]',
        '[role="menuitemradio"][aria-checked="true"]',
        'button.__composer-pill',
    ]
    for selector in selectors:
        try:
            for node in page.query_selector_all(selector):
                if DEEP_RESEARCH_TEXT.search(_node_text(node)):
                    return True
        except Exception:
            continue
    return False


def select_deep_research(page) -> bool:
    """Select and verify ChatGPT Deep Research without coordinate clicks."""
    if _deep_research_is_active(page):
        return True

    # Some layouts expose Deep Research directly beside the composer.
    for selector in DEEP_RESEARCH_CHOICES:
        try:
            for node in page.query_selector_all(selector):
                if DEEP_RESEARCH_TEXT.search(_node_text(node)):
                    node.click()
                    time.sleep(1)
                    return _deep_research_is_active(page)
        except Exception:
            continue

    for opener_selector in DEEP_RESEARCH_MENU_OPENERS:
        try:
            opener = page.query_selector(opener_selector)
            if opener is None:
                continue
            opener.click()
            time.sleep(1)
        except Exception:
            continue
        for choice_selector in DEEP_RESEARCH_CHOICES:
            try:
                for node in page.query_selector_all(choice_selector):
                    if not DEEP_RESEARCH_TEXT.search(_node_text(node)):
                        continue
                    node.click()
                    time.sleep(1)
                    return _deep_research_is_active(page)
            except Exception:
                continue
    return False


def _press_escape(page) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    time.sleep(0.4)


def _read_labeled_menu_state(page) -> dict[str, str | None]:
    state: dict[str, str | None] = {"model": None, "effort": None}
    try:
        nodes = page.query_selector_all('[role="menuitem"]')
    except Exception:
        return state
    for node in nodes:
        try:
            if not node.is_visible():
                continue
        except Exception:
            continue
        lines = [line.strip() for line in _node_text(node).splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        label = lines[0].casefold()
        if label in MODEL_ROW_LABELS:
            state["model"] = lines[-1]
        elif label in EFFORT_ROW_LABELS:
            state["effort"] = lines[-1]
    return state


def _effort_matches(value: str | None, target_effort: str) -> bool:
    if not value:
        return False
    aliases = EFFORT_ALIASES.get(target_effort, (target_effort.casefold(),))
    return value.casefold() in {alias.casefold() for alias in aliases}


def _select_labeled_menu_value(
    page,
    *,
    row_labels: set[str],
    choices: tuple[str, ...],
) -> bool:
    if not web._open_switcher(page):
        return False
    row = None
    try:
        for node in page.query_selector_all('[role="menuitem"]'):
            lines = [
                line.strip() for line in _node_text(node).splitlines() if line.strip()
            ]
            if lines and lines[0].casefold() in row_labels:
                row = node
                break
    except Exception:
        row = None
    if row is None:
        _press_escape(page)
        return False
    try:
        row.click()
        time.sleep(0.8)
    except Exception:
        _press_escape(page)
        return False

    wanted = {choice.casefold() for choice in choices}
    for selector in MODEL_ITEM_SELECTORS:
        try:
            nodes = page.query_selector_all(selector)
        except Exception:
            continue
        for node in nodes:
            lines = [
                line.strip() for line in _node_text(node).splitlines() if line.strip()
            ]
            if not lines or lines[-1].casefold() not in wanted:
                continue
            try:
                node.click()
                time.sleep(0.8)
                return True
            except Exception:
                continue
    _press_escape(page)
    return False


def _model_state_matches(state: dict[str, object], target_model: str) -> bool:
    active = state.get("model")
    if not isinstance(active, str) or active.casefold() != target_model.casefold():
        return False
    models = state.get("models")
    model_count = len(models) if isinstance(models, list) else 0
    return state.get("model_source") == "checked" or model_count <= 1


def _verify_target_model(page, target_model: str) -> bool:
    if not web._open_switcher(page):
        return False
    state = web.read_menu_state(page)
    _press_escape(page)
    return _model_state_matches(state, target_model)


def _select_target_model(page, target_model: str) -> bool:
    """Select a visible model by semantic text, then positively verify it."""
    if _verify_target_model(page, target_model):
        return True
    if not web._open_switcher(page):
        return False

    # The current-model row can be a submenu trigger. Hover each model row once
    # so the target model options become visible without coordinate clicks.
    candidates = []
    for selector in MODEL_ITEM_SELECTORS:
        try:
            candidates.extend(page.query_selector_all(selector))
        except Exception:
            continue
    for node in candidates:
        text = _node_text(node).splitlines()[0] if _node_text(node) else ""
        if not re.search(r"GPT|gpt|o\d|Claude|Gemini", text):
            continue
        try:
            node.hover()
            time.sleep(0.5)
        except Exception:
            continue

    for _ in range(2):
        target_nodes = []
        for selector in MODEL_ITEM_SELECTORS:
            try:
                target_nodes.extend(page.query_selector_all(selector))
            except Exception:
                continue
        for node in target_nodes:
            text = _node_text(node).splitlines()[0] if _node_text(node) else ""
            if text.casefold() != target_model.casefold():
                continue
            try:
                node.click()
                time.sleep(1)
            except Exception:
                continue
            if _verify_target_model(page, target_model):
                return True
            # A first click can open the model submenu rather than select it.
            if not web._open_switcher(page):
                break
        else:
            break
    _press_escape(page)
    return False


def select_required_model_effort(
    page,
    target_model: str,
    target_effort: str,
) -> str | None:
    """Select and verify the required model and effort; never accept defaults."""
    if web._open_switcher(page):
        current = _read_labeled_menu_state(page)
        _press_escape(page)
        if current["model"] is not None or current["effort"] is not None:
            if current["model"] != target_model:
                if not _select_labeled_menu_value(
                    page,
                    row_labels=MODEL_ROW_LABELS,
                    choices=(target_model,),
                ):
                    return None
            if not _effort_matches(current["effort"], target_effort):
                aliases = EFFORT_ALIASES.get(
                    target_effort,
                    (target_effort.casefold(),),
                )
                if not _select_labeled_menu_value(
                    page,
                    row_labels=EFFORT_ROW_LABELS,
                    choices=aliases,
                ):
                    return None
            if not web._open_switcher(page):
                return None
            verified = _read_labeled_menu_state(page)
            _press_escape(page)
            if (
                verified["model"] == target_model
                and _effort_matches(verified["effort"], target_effort)
            ):
                return f"{target_model} ({target_effort})"
            return None

    # Legacy model menu fallback retained for older ChatGPT layouts.
    if not _select_target_model(page, target_model):
        return None
    aliases = EFFORT_ALIASES.get(target_effort, (target_effort.casefold(),))
    for alias in aliases:
        verified, verified_name = web.select_model(
            page,
            alias,
            require_model=target_model,
        )
        if verified:
            return verified_name or f"{target_model} ({target_effort})"
    return None


def _logged_in_for_operation(context, page, *, require_composer: bool) -> bool:
    login = web.login_state(page, wait_secs=12 if require_composer else 3)
    if login == "ok":
        return True
    if login == "no" or require_composer:
        return False
    cookie_state, _ = web._cookie_state(context)
    return cookie_state == "ok"


def _ensure_logged_in_page(
    browser_arg: str | None,
    url: str,
    *,
    require_composer: bool = True,
):
    if web.sync_playwright is None:
        raise RuntimeError("playwright is not installed")
    if not web.ensure_browser(browser_arg):
        raise RuntimeError("dedicated CDP browser is unavailable")
    playwright = web.sync_playwright().start()
    try:
        browser = web.connect_cdp(playwright)
        context = web.pick_context(browser)
        if context is None:
            raise RuntimeError("logged-in browser context is unavailable")
        page = context.new_page()
    except Exception:
        playwright.stop()
        raise
    web._guard_dialogs(context, page)
    try:
        page.goto(url, wait_until="load", timeout=60000)
        time.sleep(2)
        if not _logged_in_for_operation(
            context,
            page,
            require_composer=require_composer,
        ):
            raise RuntimeError("ChatGPT login or composer could not be verified")
    except Exception:
        try:
            page.close()
        finally:
            playwright.stop()
        raise
    return playwright, page


def submit_research(run_dir: Path, state: dict[str, object], prompt: str,
                    browser_arg: str | None) -> None:
    playwright, page = _ensure_logged_in_page(browser_arg, web.CHATGPT_URL)
    try:
        target_model = str(state.get("target_model") or DEFAULT_TARGET_MODEL)
        target_effort = str(state.get("target_effort") or DEFAULT_TARGET_EFFORT)
        verified_model = select_required_model_effort(
            page,
            target_model,
            target_effort,
        )
        if not verified_model:
            raise RuntimeError(
                "required model or reasoning effort could not be selected and verified"
            )
        if not select_deep_research(page):
            raise RuntimeError("Deep Research mode could not be selected and verified")

        base_user = web.count_msgs_strict(page, web.USER_MSG_SELECTORS)
        base_assistant = web.count_msgs_strict(page, web.ASSISTANT_MSG_SELECTORS)
        base_copy = web.count_msgs_strict(page, web.COPY_BTN_SELECTORS)
        base_ids = sorted(web.msg_id_set(page))

        web.put_text(page, prompt)
        if not web.composer_has_prompt(page, prompt):
            web.clear_composer(page)
            web.put_text(page, prompt)
            if not web.composer_has_prompt(page, prompt):
                raise RuntimeError("prompt was not fully inserted; submission aborted")
        # click_send returns False after its Enter fallback even though Enter may
        # have submitted. Conversation identity, not that return value, is the
        # authoritative send confirmation.
        web.click_send(page)
        conversation_url = web.capture_conv_url(page)
        if not conversation_url:
            raise SentUnknownLocationError(
                "prompt may be sent but conversation URL was not captured; do not resubmit automatically"
            )

        state.update(
            {
                "status": "PROMPT_SUBMITTED",
                "conversation_url": conversation_url,
                "base_user": base_user,
                "base_assistant": base_assistant,
                "base_copy": base_copy,
                "base_message_ids": base_ids,
                "verified_model": target_model,
                "verified_effort": target_effort,
                "error": None,
            }
        )
        save_state(run_dir, state)
    finally:
        try:
            page.close()
        finally:
            playwright.stop()


def _assistant_links(node) -> list[str]:
    if node is None:
        return []
    try:
        links = node.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href).filter(Boolean)",
        )
    except Exception:
        return []
    return list(dict.fromkeys(str(link) for link in links if str(link).startswith("http")))


def _deep_research_report(page, *, wait_seconds: float = 0) -> dict[str, object] | None:
    """Read a positively completed Deep Research report from its sandbox frame."""
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        _reveal_latest_turn(page)
        try:
            frames = list(page.frames)
        except Exception:
            return None
        for outer in frames:
            if not outer.url.startswith(
                "https://connector-openai-deep-research.web-sandbox.oaiusercontent.com/"
            ):
                continue
            try:
                frame_element = outer.frame_element()
                if frame_element.get_attribute("title") != "internal://deep-research":
                    continue
                children = list(outer.child_frames)
            except Exception:
                continue
            for frame in children:
                if frame.name != "root" or frame.url != "about:blank":
                    continue
                try:
                    marker = frame.get_by_text("Deep research completed.", exact=True)
                    if marker.count() != 1:
                        continue
                    report = marker.locator(
                        "xpath=ancestor::div[contains(@class, '_reportPage_')][1]"
                    )
                    if report.count() != 1:
                        continue
                    text = (report.inner_text() or "").strip()
                    links = report.locator("a[href]").evaluate_all(
                        "els => els.map(el => el.href).filter(Boolean)"
                    )
                except Exception:
                    continue
                normalized_links = _normalize_links(links)
                if len(text) < 300 or len(normalized_links) < 2:
                    continue
                report_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                return {
                    "message_id": f"deep-research-report:{report_hash}",
                    "text": text,
                    "links": normalized_links,
                }
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _reveal_latest_turn(page) -> None:
    """Scroll the conversation surface so lazy Deep Research cards are mounted."""
    try:
        page.evaluate(
            """() => {
              window.scrollTo(0, document.body.scrollHeight);
              for (const el of document.querySelectorAll('main, main *')) {
                if (el.scrollHeight > el.clientHeight + 20) {
                  el.scrollTop = el.scrollHeight;
                }
              }
            }"""
        )
    except Exception:
        pass


def _is_new_completed_turn(page, state: dict[str, object], node) -> bool:
    """Require durable pre-submit baselines and a newly completed assistant turn."""
    if node is None:
        return False
    try:
        base_assistant = int(state["base_assistant"])
        base_copy = int(state["base_copy"])
        base_message_ids = set(state["base_message_ids"])
        message_id = node.get_attribute("data-message-id") or ""
    except (KeyError, TypeError, ValueError):
        return False
    if not message_id or message_id in base_message_ids:
        return False
    return web.last_turn_complete(
        page,
        base_assistant=base_assistant,
        base_copy=base_copy,
    )


def _valid_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _normalize_links(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            link
            for link in value
            if isinstance(link, str) and link.startswith(("http://", "https://"))
        )
    )


def _valid_chatgpt_conversation_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "chatgpt.com"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.query == ""
        and parsed.fragment == ""
        and bool(
            re.fullmatch(
                r"/c/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?",
                parsed.path,
                re.I,
            )
        )
    )


def _normalized_report_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _harvested_report_matches(observed: str, harvested: str) -> bool:
    if len(harvested) < 300 or not DEEP_RESEARCH_DONE_TEXT.search(harvested):
        return False
    observed_normalized = _normalized_report_text(observed)
    harvested_normalized = _normalized_report_text(harvested)
    return bool(observed_normalized) and observed_normalized == harvested_normalized


def apply_submission_observation(
    run_dir: Path,
    state: dict[str, object],
    observation: dict[str, object],
) -> dict[str, object]:
    """Bind an agent-driven browser submission only after positive verification."""
    if state.get("status") != "CREATED":
        raise ValueError("submission observation requires a CREATED run")
    if state.get("browser_driver") != "agent":
        raise ValueError("submission observation requires browser_driver=agent")

    target_model = str(state.get("target_model") or "")
    target_effort = str(state.get("target_effort") or "")
    verified_model = observation.get("verified_model")
    verified_effort = observation.get("verified_effort")
    conversation_url = observation.get("conversation_url")
    base_ids = observation.get("base_message_ids")
    required_checks = {
        "model": isinstance(verified_model, str)
        and verified_model.casefold() == target_model.casefold(),
        "effort": isinstance(verified_effort, str)
        and verified_effort.casefold() == target_effort.casefold(),
        "deep research": observation.get("deep_research_active") is True,
        "prompt": observation.get("prompt_verified") is True,
        "conversation URL": _valid_chatgpt_conversation_url(conversation_url),
        "base assistant count": _valid_nonnegative_int(
            observation.get("base_assistant")
        ),
        "base copy count": _valid_nonnegative_int(observation.get("base_copy")),
        "base message IDs": isinstance(base_ids, list)
        and all(isinstance(item, str) and item for item in base_ids),
    }
    failed = [name for name, passed in required_checks.items() if not passed]
    if failed:
        raise ValueError(
            "submission observation failed verification: " + ", ".join(failed)
        )

    state.update(
        {
            "status": "PROMPT_SUBMITTED",
            "conversation_url": conversation_url,
            "base_assistant": observation["base_assistant"],
            "base_copy": observation["base_copy"],
            "base_message_ids": list(base_ids),
            "verified_model": verified_model,
            "verified_effort": verified_effort,
            "error": None,
        }
    )
    save_state(run_dir, state)
    return state


def apply_refresh_observation(
    run_dir: Path,
    state: dict[str, object],
    observation: dict[str, object],
    *,
    required_driver: str,
) -> dict[str, object]:
    """Classify one browser observation without depending on its transport."""
    if state.get("browser_driver") != required_driver:
        raise ValueError(
            f"refresh observation requires browser_driver={required_driver}"
        )
    if state.get("status") in {"COMPLETED", "HARVESTED"}:
        return state
    expected_url = str(state.get("conversation_url") or "")
    observed_url = observation.get("conversation_url")
    if not _valid_chatgpt_conversation_url(expected_url):
        raise ValueError("run has no valid ChatGPT conversation URL")
    if not _valid_chatgpt_conversation_url(observed_url) or observed_url != expected_url:
        raise ValueError("refresh observation does not match the bound conversation")

    text = observation.get("assistant_text")
    response = observation.get("response_text")
    message_id = observation.get("message_id")
    base_ids = state.get("base_message_ids")
    if not isinstance(text, str):
        text = ""
    if not isinstance(response, str):
        response = text
    if not isinstance(base_ids, list):
        base_ids = []
    links = _normalize_links(observation.get("links"))
    new_completed_turn = (
        observation.get("turn_complete") is True
        and observation.get("terminal_signal")
        in {"assistant_turn_complete", "deep_research_report_frame"}
        and isinstance(message_id, str)
        and bool(message_id)
        and message_id not in base_ids
    )
    quota = observation.get("quota")

    if (
        new_completed_turn
        and len(text) >= 300
        and len(links) >= 2
        and DEEP_RESEARCH_DONE_TEXT.search(text)
    ):
        if not _harvested_report_matches(text, response):
            raise ValueError("harvested report does not match the verified assistant report")
        atomic_write_text(run_dir / "response.md", response.rstrip() + "\n")
        atomic_write_text(
            run_dir / "sources.json",
            json.dumps({"sources": links}, ensure_ascii=False, indent=2) + "\n",
        )
        state.update(
            {
                "status": "COMPLETED",
                "response_file": "response.md",
                "sources_file": "sources.json",
                "source_count": len(links),
                "error": None,
            }
        )
    elif isinstance(quota, str) and quota.strip():
        state.update({"status": "FAILED", "error": f"quota: {quota[:200]}"})
    elif observation.get("streaming") is True:
        state.update({"status": "RESEARCHING", "error": None})
    elif new_completed_turn and text and DEEP_RESEARCH_CLARIFICATION_TEXT.search(text):
        atomic_write_text(run_dir / "clarification.md", text.rstrip() + "\n")
        state.update(
            {
                "status": "WAITING_CLARIFICATION",
                "clarification_file": "clarification.md",
                "error": None,
            }
        )
    else:
        state.update({"status": "RESEARCHING", "error": None})
    save_state(run_dir, state)
    return state


def refresh_research(run_dir: Path, state: dict[str, object],
                     browser_arg: str | None) -> dict[str, object]:
    if state.get("browser_driver") != "cdp":
        raise ValueError("CDP refresh requires browser_driver=cdp")
    if state["status"] in {"COMPLETED", "HARVESTED"}:
        return state
    conversation_url = str(state.get("conversation_url") or "")
    if not _valid_chatgpt_conversation_url(conversation_url):
        raise RuntimeError("run has no valid ChatGPT conversation URL")

    playwright, page = _ensure_logged_in_page(
        browser_arg,
        conversation_url,
        require_composer=False,
    )
    try:
        _reveal_latest_turn(page)
        quota = web.detect_quota_block(page)
        report = _deep_research_report(page, wait_seconds=20)
        node = None if report else web.last_assistant_node(page)
        text = str(report["text"]) if report else _node_text(node)
        links = list(report["links"]) if report else _assistant_links(node)
        new_completed_turn = bool(report) or _is_new_completed_turn(page, state, node)
        response = text
        if not report and (
            new_completed_turn
            and len(text) >= 300
            and len(links) >= 2
            and DEEP_RESEARCH_DONE_TEXT.search(text)
        ):
            base_copy = int(state.get("base_copy") or 0)
            response = web.copy_last_turn(page, base_copy=base_copy, expected=text) or text
        message_id = str(report["message_id"]) if report else ""
        if not report and node is not None:
            try:
                message_id = node.get_attribute("data-message-id") or ""
            except Exception:
                pass
        return apply_refresh_observation(
            run_dir,
            state,
            {
                "kind": "refresh",
                "conversation_url": conversation_url,
                "message_id": message_id,
                "assistant_text": text,
                "response_text": response,
                "links": links,
                "turn_complete": new_completed_turn,
                "terminal_signal": (
                    "deep_research_report_frame"
                    if report
                    else "assistant_turn_complete"
                    if new_completed_turn
                    else None
                ),
                "streaming": web.is_streaming(page),
                "quota": quota,
            },
            required_driver="cdp",
        )
    finally:
        try:
            page.close()
        finally:
            playwright.stop()


def start_command(args: argparse.Namespace) -> int:
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    run_dir, state = create_run(
        prompt,
        Path(args.out_dir),
        browser_driver=args.browser_driver,
    )
    if not args.dry_run and args.browser_driver == "cdp":
        try:
            output = sys.stderr if args.json else sys.stdout
            with contextlib.redirect_stdout(output):
                submit_research(run_dir, state, prompt, args.browser)
        except SentUnknownLocationError as exc:
            state.update({"status": "SENT_UNKNOWN_LOCATION", "error": str(exc)[:500]})
            save_state(run_dir, state)
            print(f"Deep Research submission location is unknown: {exc}", file=sys.stderr)
            return 4
        except Exception as exc:
            state.update({"status": "FAILED", "error": str(exc)[:500]})
            save_state(run_dir, state)
            print(f"Deep Research submission failed: {exc}", file=sys.stderr)
            return 1
    payload = {"run_dir": _display_path(run_dir), "run_id": state["run_id"], "status": state["status"],
               "conversation_url": state.get("conversation_url")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Deep Research run {state['status']}: {run_dir}")
    return 0


def record_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    try:
        state = load_state(run_dir)
        observation_path = Path(args.observation_file).expanduser().resolve()
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        if not isinstance(observation, dict):
            raise ValueError("observation must be a JSON object")
        kind = observation.get("kind")
        if kind == "submission":
            state = apply_submission_observation(run_dir, state, observation)
        elif kind == "refresh":
            state = apply_refresh_observation(
                run_dir,
                state,
                observation,
                required_driver="agent",
            )
        else:
            raise ValueError("observation kind must be submission or refresh")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Deep Research observation rejected: {exc}", file=sys.stderr)
        return 2
    payload = {
        "run_dir": _display_path(run_dir),
        "run_id": state["run_id"],
        "status": state["status"],
        "conversation_url": state.get("conversation_url"),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Deep Research observation recorded: {state['status']}")
    _harden_run_permissions(run_dir)
    return 0


def status_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    try:
        state = load_state(run_dir)
        _harden_run_permissions(run_dir)
        if args.refresh:
            if state.get("browser_driver") != "cdp":
                raise ValueError("status --refresh requires browser_driver=cdp")
            output = sys.stderr if args.json else sys.stdout
            with contextlib.redirect_stdout(output):
                state = refresh_research(run_dir, state, args.browser)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        state["error"] = str(exc)[:500]
        save_state(run_dir, state)
        print(f"Deep Research refresh failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    else:
        print(f"{state['run_id']}: {state['status']}")
    return 0


def fetch_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    try:
        state = load_state(run_dir)
        _harden_run_permissions(run_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if state["status"] == "HARVESTED" and (run_dir / "report.md").is_file():
        payload = {"run_dir": _display_path(run_dir), "run_id": state["run_id"], "status": state["status"],
                   "report_file": _display_path(run_dir / "report.md")}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"Deep Research report already harvested: {run_dir / 'report.md'}")
        return 0
    if state["status"] != "COMPLETED":
        print(f"Run is not complete: {state['status']}", file=sys.stderr)
        return 3
    response_path = run_dir / "response.md"
    try:
        response = response_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Completed run has no readable response: {exc}", file=sys.stderr)
        return 2
    if not response.strip():
        print("Completed run response is empty", file=sys.stderr)
        return 2

    atomic_write_text(run_dir / "report.md", response)
    state["status"] = "HARVESTED"
    state["report_file"] = "report.md"
    state["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_write_text(
        run_dir / "state.json",
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )
    _harden_run_permissions(run_dir)
    payload = {"run_dir": _display_path(run_dir), "run_id": state["run_id"], "status": state["status"],
               "report_file": _display_path(run_dir / "report.md")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Harvested Deep Research report: {run_dir / 'report.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Subscription ChatGPT Deep Research bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create and submit a Deep Research run")
    start.add_argument("--prompt-file", required=True)
    start.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    start.add_argument("--dry-run", action="store_true")
    start.add_argument(
        "--browser-driver",
        choices=("cdp", "agent"),
        default="cdp",
        help="Use direct CDP automation or prepare a run for an agent browser",
    )
    start.add_argument("--browser", default=None)
    start.add_argument("--json", action="store_true")
    start.set_defaults(handler=start_command)

    status = subparsers.add_parser("status", help="Read the last persisted run status")
    status.add_argument("run_dir")
    status.add_argument("--refresh", action="store_true")
    status.add_argument("--browser", default=None)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=status_command)

    fetch = subparsers.add_parser("fetch", help="Persist a completed Deep Research report")
    fetch.add_argument("run_dir")
    fetch.add_argument("--json", action="store_true")
    fetch.set_defaults(handler=fetch_command)

    record = subparsers.add_parser(
        "record",
        help="Record a fail-closed observation from an agent browser",
    )
    record.add_argument("run_dir")
    record.add_argument("--observation-file", required=True)
    record.add_argument("--json", action="store_true")
    record.set_defaults(handler=record_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(effective_argv)
    if args.command == "status" and args.refresh:
        try:
            state = load_state(Path(args.run_dir).expanduser().resolve())
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if state.get("browser_driver") != "cdp":
            print("status --refresh requires browser_driver=cdp", file=sys.stderr)
            return 2
    reexec_result = _maybe_reexec_on_windows(args)
    if reexec_result is not None:
        return reexec_result
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
