from __future__ import annotations

import re
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings
from app.core.logging import get_logger
from app.core.memory import MemoryStore

logger = get_logger("job_agent")

_REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh", "hybrid")
_PROFILE_VALUE_SOURCES = ("preferences", "identity")


class JobListing:
    def __init__(self, site: str, title: str, company: str, location: str, url: str) -> None:
        self.site = site
        self.title = (title or "").strip()
        self.company = (company or "").strip()
        self.location = (location or "").strip()
        self.url = (url or "").strip()

    def to_dict(self) -> dict[str, str]:
        return {
            "site": self.site,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
        }


class JobAgent:
    """Autonomous job-search-and-apply workflow driven through a real browser."""

    def __init__(self, settings: Settings, memory: MemoryStore, session: Any) -> None:
        self.settings = settings
        self.memory = memory
        self.session = session
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._profile: dict[str, Any] = {}
        self._site_profiles: dict[str, Any] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        profile_path = Path(self.settings.job_profile_path).expanduser()
        site_path = Path(self.settings.job_site_profiles_path).expanduser()
        try:
            if profile_path.exists():
                self._profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("failed to load job profile %s: %s", profile_path, exc)
        try:
            if site_path.exists():
                data = yaml.safe_load(site_path.read_text(encoding="utf-8")) or {}
                self._site_profiles = data.get("sites") or {}
        except Exception as exc:
            logger.warning("failed to load site profiles %s: %s", site_path, exc)

    # ---- public API -------------------------------------------------------

    def start_apply_run(
        self,
        query: str,
        sites: list[str] | None = None,
        max_applications: int | None = None,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValueError("Missing search query.")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._snapshot()
            task: dict[str, Any] = {
                "task_id": str(uuid.uuid4())[:8],
                "query": query,
                "phase": "starting",
                "status": "running",
                "applied": 0,
                "skipped": 0,
                "failed": 0,
                "paused": 0,
                "sites": sites or list(self.settings.job_target_sites),
                "max_applications": int(max_applications or self.settings.job_max_apply_per_run),
                "progress": [],
                "active_job": None,
                "paused_reason": None,
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            }
            self.memory.set_state("active_job_task", task)
            self._thread = threading.Thread(
                target=self._run,
                args=(query, task),
                daemon=True,
                name="jarvis-job-agent",
            )
            self._thread.start()
        return self._snapshot()

    def status(self) -> dict[str, Any]:
        return self._snapshot()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            task = self.memory.get_state("active_job_task", default={})
            if not task:
                return {"status": "idle", "running": False}
            task["status"] = "canceled"
            self.memory.set_state("active_job_task", task)
        logger.info("job agent run canceled by request")
        return self._snapshot()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._snapshot()
            task = self.memory.get_state("active_job_task", default={})
            if not task or task.get("status") != "paused":
                return self._snapshot()
            if not task.get("resume_skip_url"):
                task["resume_skip_url"] = task.get("paused_url")
            task["status"] = "running"
            task["phase"] = "resuming"
            task["paused_reason"] = None
            self.memory.set_state("active_job_task", task)
            query = str(task.get("query", "")).strip()
            self._thread = threading.Thread(
                target=self._run,
                args=(query, task),
                daemon=True,
                name="jarvis-job-agent",
            )
            self._thread.start()
        logger.info("job agent run resumed")
        return self._snapshot()

    def applied(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.memory.recent_job_applications(limit=limit)

    def applied_totals(self) -> dict[str, int]:
        return self.memory.job_applications_summary()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _snapshot(self) -> dict[str, Any]:
        state = self.memory.get_state("active_job_task", default={})
        if state:
            state["running"] = self.is_running()
            summary = self.memory.job_applications_summary()
            state["totals"] = summary
        return state or {"status": "idle", "running": False}

    # ---- workflow ---------------------------------------------------------

    def _update(self, task: dict[str, Any], **fields: Any) -> None:
        task.update(fields)
        task["updated_at"] = datetime.now(UTC).isoformat()
        # Track interview stage and next action if provided
        if "interview_stage" in fields:
            task["interview_stage"] = fields["interview_stage"]
        if "next_action" in fields:
            task["next_action"] = fields["next_action"]
        if "deadline" in fields:
            task["deadline"] = fields["deadline"]
        if "notes" in fields:
            task["notes"] = fields["notes"]
        self.memory.set_state("active_job_task", task)

    def _run(self, query: str, task: dict[str, Any]) -> None:
        targets = [site for site in task["sites"] if self._site_profiles.get(site)]
        if not targets:
            targets = ["linkedin", "indeed"]
        resume_skip_url = task.get("resume_skip_url")

        try:
            for site in targets:
                if self._interrupted(task):
                    return
                profile = self._site_profiles[site]
                self._update(task, phase=f"searching {site}")
                listings = self._search_site(profile, query, task)
                self._update(task, phase=f"applying on {site}", progress=task["progress"] + [f"{site}: {len(listings)} found"])
                for job in listings:
                    if self._interrupted(task):
                        return
                    if task["applied"] >= task["max_applications"]:
                        self._update(task, phase="done")
                        return
                    if resume_skip_url and job.url == resume_skip_url:
                        resume_skip_url = None
                        continue
                    if self.memory.is_job_applied(job.url):
                        task["skipped"] += 1
                        continue
                    outcome = self._apply_to_job(profile, job, task)
                    if outcome == "paused":
                        self._update(task, phase="paused", status="paused", paused_reason=task["paused_reason"])
                        return
                    status, note = outcome
                    self.memory.log_job_application(
                        site=site,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        url=job.url,
                        status=status,
                        notes=note,
                        source=site,
                    )
                    if status == "applied":
                        task["applied"] += 1
                    elif status == "failed":
                        task["failed"] += 1
                    else:
                        task["skipped"] += 1
                    self._update(task, active_job=job.to_dict())
            self._update(task, phase="done", status="completed", finished_at=datetime.now(UTC).isoformat(),
            interview_stage=task.get("interview_stage"), next_action=task.get("next_action"),
            deadline=task.get("deadline"), notes=task.get("notes"))
        except Exception as exc:
            logger.exception("job agent run failed")
            self._update(task, phase="failed", status="failed", paused_reason=str(exc), finished_at=datetime.now(UTC).isoformat())

    def _interrupted(self, task: dict[str, Any]) -> bool:
        return task.get("status") == "canceled"

    # ---- search -----------------------------------------------------------

    def _build_search_url(self, profile: dict[str, Any], query: str) -> str:
        base = str(profile.get("base_url", "")).rstrip("/")
        path = str(profile.get("search_path", ""))
        params: dict[str, str] = {}
        for key, template in (profile.get("search_params") or {}).items():
            value = str(template)
            if "{query}" in value:
                value = value.replace("{query}", query)
            if "{location}" in value:
                location = self.settings.job_apply_remote_only and "remote" or self._location_hint()
                value = value.replace("{location}", location or "")
            if value:
                params[key] = value
        encoded = urllib.parse.urlencode(params)
        return f"{base}{path}" + (f"?{encoded}" if encoded else "")

    def _location_hint(self) -> str:
        identity = self._profile.get("identity") or {}
        return str(identity.get("location", "")).strip()

    def _search_site(self, profile: dict[str, Any], query: str, task: dict[str, Any]) -> list[JobListing]:
        url = self._build_search_url(profile, query)
        host = self._host_of(profile)
        page = self.session.ensure_page(url, [host] if host else ["http"])
        results_selector = str(profile.get("results_selector", "")).strip()
        card = profile.get("card") or {}

        if results_selector:
            try:
                self.session.wait_for(page, results_selector, timeout_ms=20000)
            except Exception:
                logger.info("no results selector found on %s", url)

        link_selector = str(card.get("link") or "").strip() or results_selector or "a[href]"
        cards_links = self.session.extract_text(page, link_selector, attribute="href", multiple=True)
        unique_links: list[str] = []
        for link in cards_links:
            resolved = urllib.parse.urljoin(url, link)
            if resolved not in unique_links and resolved != url:
                unique_links.append(resolved)

        titles = self.session.extract_text(page, card.get("title") or "h2 a, h3 a", multiple=True) if card.get("title") else []
        companies = self.session.extract_text(page, card.get("company") or "", multiple=True) if card.get("company") else []
        locations = self.session.extract_text(page, card.get("location") or "", multiple=True) if card.get("location") else []

        listings: list[JobListing] = []
        count = max(len(unique_links), len(titles), len(companies), len(locations))
        for index in range(count):
            title = titles[index] if index < len(titles) else "Untitled role"
            company = companies[index] if index < len(companies) else ""
            location = locations[index] if index < len(locations) else ""
            link = unique_links[index] if index < len(unique_links) else ""
            if not link:
                continue
            listings.append(JobListing(site=profile.get("name", "generic"), title=title, company=company, location=location, url=link))
            if len(listings) >= 20:
                break
        return listings

    @staticmethod
    def _host_of(profile: dict[str, Any]) -> str:
        base = str(profile.get("base_url", "")).strip()
        try:
            return urllib.parse.urlparse(base).netloc or ""
        except Exception:
            return ""

    # ---- apply ------------------------------------------------------------

    def _apply_to_job(self, profile: dict[str, Any], job: JobListing, task: dict[str, Any]) -> tuple[str, str]:
        if not profile.get("easy_apply"):
            return "skipped", "site does not expose an in-page Easy Apply flow (easy_apply=false)"
        host = self._host_of(profile)
        try:
            page = self.session.ensure_page(job.url, [host] if host else ["http"])
        except Exception as exc:
            return "failed", f"could not open job: {exc}"

        if self._detect_captcha(page):
            task["paused_reason"] = f"CAPTCHA detected on {job.site} ({job.title})"
            task["paused"] += 1
            task["paused_url"] = job.url
            self._update(task, active_job=job.to_dict())
            return "paused", "captcha"

        login_selector = self._login_selector(page, profile)
        if login_selector:
            task["paused_reason"] = f"Login required on {job.site} - sign in and resume"
            task["paused"] += 1
            task["paused_url"] = job.url
            self._update(task, active_job=job.to_dict())
            return "paused", "login"

        apply_button = str(profile.get("apply_button", "")).strip()
        if not apply_button:
            return "skipped", "no apply button configured"

        try:
            try:
                self.session.click(page, apply_button)
            except Exception:
                return "skipped", "apply button not found"
            time.sleep(1.5)
        except Exception as exc:
            return "failed", f"apply click failed: {exc}"

        filled = self._fill_form(page)
        resume_result = self._upload_resume(page)
        submitted = self._submit(page, profile)

        note = f"fields={filled} resume={resume_result} submit={'yes' if submitted else 'no'}"
        if submitted:
            return "applied", note
        # If submit not found, attempt one more fill+submit pass (multi-step forms).
        time.sleep(1.0)
        filled_again = self._fill_form(page)
        if filled_again and self._submit(page, profile):
            return "applied", note + f" pass2_fields={filled_again}"
        return "failed", note + " no submit button"

    def _login_selector(self, page: Any, profile: dict[str, Any]) -> str:
        for selector in profile.get("login_check") or []:
            try:
                if page.locator(selector).count() > 0:
                    return selector
            except Exception:
                continue
        return ""

    def _detect_captcha(self, page: Any) -> bool:
        selectors = ["iframe[title*='captcha']", ".g-recaptcha", "iframe[src*='recaptcha']", "input#captcha-form"]
        for selector in selectors:
            try:
                if page.locator(selector).count() > 0:
                    logger.info("captcha detected via selector %s", selector)
                    return True
            except Exception:
                continue
        try:
            body = (page.locator("body").inner_text(timeout=2000) or "").lower()
            if "not a robot" in body or "captcha" in body:
                return True
        except Exception:
            pass
        return False

    def _collect_inputs(self, page: Any) -> list[dict[str, Any]]:
        expression = """
        () => Array.from(document.querySelectorAll('input, textarea, select')).map((el, i) => {
            let label = '';
            if (el.closest('label')) label = el.closest('label').innerText || '';
            const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            return {
                i: i,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                aria: el.getAttribute('aria-label') || '',
                label: label,
                visible: visible && el.type !== 'hidden' && el.type !== 'password'
            };
        }).filter(x => x.visible)
        """
        try:
            return list(self.session.evaluate(page, expression) or [])
        except Exception:
            return []

    def _profile_value(self, field: str) -> str:
        prefs = self._profile.get("preferences") or {}
        identity = self._profile.get("identity") or {}
        if field in prefs and prefs[field] is not None:
            return str(prefs[field])
        if field in identity and identity[field] is not None:
            return str(identity[field])
        if field == "summary":
            return str(self._profile.get("summary", ""))
        return ""

    def _match_field(self, field: str, input_meta: dict[str, Any]) -> bool:
        patterns = (self._profile.get("form_field_patterns") or {}).get(field) or []
        if not patterns:
            return False
        haystack = " ".join(
            str(input_meta.get(key, "")).lower()
            for key in ("placeholder", "aria", "label", "name", "id")
        )
        return any(re.search(pattern, haystack) for pattern in patterns)

    def _fill_form(self, page: Any) -> int:
        filled = 0
        inputs = self._collect_inputs(page)
        if not inputs:
            return 0
        for meta in inputs:
            tag = meta.get("tag")
            if tag == "file":
                continue
            value = ""
            for field in ("full_name", "first_name", "last_name", "email", "phone", "linkedin", "github", "website", "location", "current_company", "current_title", "years_experience", "salary_expectation", "work_authorization", "notice_period", "cover_letter"):
                if self._match_field(field, meta):
                    value = self._profile_value(field)
                    break
            if not value:
                continue
            locator = self._input_locator(page, meta)
            if locator is None:
                continue
            try:
                if tag == "select":
                    if self._select_option(locator, value):
                        filled += 1
                else:
                    locator.first.fill(value, timeout=5000)
                    filled += 1
            except Exception as exc:
                logger.debug("fill failed for %s: %s", meta.get("name"), exc)
        return filled

    @staticmethod
    def _input_locator(page: Any, meta: dict[str, Any]) -> Any | None:
        name = str(meta.get("name") or "").strip()
        element_id = str(meta.get("id") or "").strip()
        if name:
            return page.locator(f"[name='{name.replace(chr(39), chr(92) + chr(39))}']")
        if element_id:
            return page.locator(f"[id='{element_id.replace(chr(39), chr(92) + chr(39))}']")
        return None

    def _select_option(self, select_locator: Any, value: str) -> bool:
        options = select_locator.locator("option")
        count = options.count()
        wanted = value.lower()
        for index in range(count):
            text = (options.nth(index).inner_text() or "").strip().lower()
            if text and (text == wanted or wanted in text):
                select_locator.first.select_option(index=index, timeout=5000)
                return True
        return False

    def _upload_resume(self, page: Any) -> str:
        resume = Path(self.settings.resume_pdf_path).expanduser()
        if not resume.is_file():
            return f"no-resume-file:{resume.name if resume.name else 'CV.pdf'}"
        if resume.stat().st_size == 0:
            return "resume-empty"
        file_input = page.locator("input[type='file']")
        if file_input.count() == 0:
            return "no-upload-field"
        try:
            file_input.first.set_input_files(str(resume))
            return "uploaded"
        except Exception as exc:
            return f"upload-failed:{exc}"

    def _submit(self, page: Any, profile: dict[str, Any]) -> bool:
        submit_buttons = list(profile.get("submit_buttons") or [])
        submit_buttons.extend(["button[type='submit']", "button[aria-label*='submit' i]", "button[aria-label*='apply now' i]"])
        for selector in submit_buttons:
            try:
                if page.locator(selector).count() > 0:
                    page.locator(selector).first.click(timeout=5000)
                    time.sleep(2.0)
                    return True
            except Exception:
                continue
        return False


_job_agent_singleton: JobAgent | None = None
_job_agent_lock = threading.Lock()


def get_job_agent(settings: Settings, memory: MemoryStore) -> JobAgent:
    """Shared JobAgent instance so the tool registry and API routes see the same task state."""
    from app.tools.browser_session import get_browser_session

    global _job_agent_singleton
    if _job_agent_singleton is None:
        with _job_agent_lock:
            if _job_agent_singleton is None:
                _job_agent_singleton = JobAgent(settings, memory, get_browser_session(settings))
    return _job_agent_singleton
