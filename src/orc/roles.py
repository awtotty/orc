import os

ROLES_DIR = ".roles"

# roles/ directory at the orc repo root
_ORC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROLES_PATH = os.path.join(_ORC_ROOT, "roles")


def _load_file(name):
    path = os.path.join(_ROLES_PATH, name)
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def default_role_content(role_name):
    """Load system.md + role-specific prompt."""
    system = _load_file("system.md")
    role = _load_file(f"{role_name}.md")
    if not role:
        role = f"# {role_name}\n\nNo instructions defined for this role.\n"
    return system + "\n" + role


def available_roles():
    """List all available role names from the roles/ directory."""
    if not os.path.isdir(_ROLES_PATH):
        return []
    return [
        os.path.splitext(f)[0]
        for f in sorted(os.listdir(_ROLES_PATH))
        if f.endswith(".md") and f != "system.md"
    ]
