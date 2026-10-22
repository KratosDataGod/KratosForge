"""Notebook environments: what the app calls (environments.py has the folders and installs)."""

from __future__ import annotations

import re
from typing import Any

from . import app_settings
from .environments import Environments
from .kernel_proc import ProcessKernel


class EnvironmentMixin:
    @property
    def environments(self) -> Environments:
        if getattr(self, "_environments", None) is None:
            self._environments = Environments(self.root)  # type: ignore[attr-defined]
        return self._environments

    def _prepare_kernel(self, notebook: str, kernel: Any) -> None:
        """A notebook's new process kernel gets its environment's packages."""
        if isinstance(kernel, ProcessKernel) and notebook:
            kernel.library_paths = self.environments.paths_for(notebook)
            kernel.calls["install_packages"] = lambda specs: self.notebook_packages_install(notebook, specs)
            kernel.calls["package_job"] = self.environments.job  # %pip follows its install until pip finishes

    def notebook_environments(self, notebook: str | None = None) -> dict[str, Any]:
        """Every environment, and the one ``notebook`` uses."""
        return {"environments": self.environments.list(), "current": self.environments.of(notebook) if notebook else None,
                "process": bool(app_settings.value("kernel_process")),
                "allowed": app_settings.value("allow_notebook_packages") is not False}

    def environment_create(self, name: str) -> dict[str, Any]:
        return self.environments.create(name)

    def environment_delete(self, name: str) -> dict[str, Any]:
        return self.environments.delete(name)

    def environment_attach(self, notebook: str, name: str | None) -> dict[str, Any]:
        """Use ``name`` (or none) for ``notebook``. Its session restarts with it the next time it runs."""
        host = self.sessions.host_of(notebook)  # type: ignore[attr-defined]
        if host:
            raise RuntimeError(f"{notebook} shares {host}'s session, which uses {host}'s environment. Leave the "
                               "shared session (Sessions) to change its environment.")
        out = self.environments.attach(notebook, name)
        session = self.sessions.session(notebook, create=False)  # type: ignore[attr-defined]
        restart = bool(session and isinstance(session.kernel, ProcessKernel) and session.kernel.alive())
        if session is not None:
            self._prepare_kernel(notebook, session.kernel)
        return out | {"restart_needed": restart}

    def environment_install(self, name: str, packages: list[str]) -> dict[str, Any]:
        self._packages_allowed()
        return self.environments.start(name, packages)

    def environment_uninstall(self, name: str, packages: list[str]) -> dict[str, Any]:
        return self.environments.start(name, packages, remove=True)

    def environment_job(self, job_id: str) -> dict[str, Any]:
        return self.environments.job(job_id)

    def notebook_packages_install(self, notebook: str, packages: list[str]) -> dict[str, Any]:
        """%pip install from a notebook cell: into the notebook's environment, made for it if it has none."""
        self._packages_allowed()
        env = self.environments.of(notebook)
        if env is None:
            base = re.sub(r"[^A-Za-z0-9 _.-]+", "_", notebook).strip(" ._")[:50] or "Notebook"
            env = base if base not in {e["name"] for e in self.environments.list()} else f"{base} packages"
            self.environments.create(env)
            self.environment_attach(notebook, env)
        return self.environments.start(env, packages)

    def _packages_allowed(self) -> None:
        if app_settings.value("allow_notebook_packages") is False:
            raise PermissionError("Your organization doesn't allow installing packages for notebooks on this PC.")
        if not app_settings.value("kernel_process"):
            raise RuntimeError("Notebook environments need Run notebooks in their own process (Settings > Preview features).")
