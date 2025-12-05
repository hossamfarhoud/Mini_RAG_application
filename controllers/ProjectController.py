from .BaseController import BaseController
from pathlib import Path
import re


class ProjectController(BaseController):
    def __init__(self):
        super().__init__()

    @staticmethod
    def _sanitize_project_id(pid) -> str:
        # Convert to string if it's an int
        pid = str(pid) if pid is not None else ""
        
        # Remove spaces, newline (\n), and carriage return (\r)
        pid = pid.strip()
        if not pid:
            raise ValueError("project_id is required")

        # Allow only alphanumeric characters, underscores, and hyphens
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", pid):
            raise ValueError("Invalid project_id")

        return pid

    def get_project_path(self, project_id) -> str:
        # Sanitize project_id to ensure it's safe for file system use
        pid = self._sanitize_project_id(project_id)

        # Get the base path to the 'files' directory (from BaseController)
        base = Path(self.files_dir)

        # Build the full project directory path
        project_dir = base / pid  # Using pathlib avoids Windows backslash issues

        # Create the directory if it doesn't exist
        project_dir.mkdir(parents=True, exist_ok=True)

        # Return the path as a string
        return str(project_dir)