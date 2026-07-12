"""Conservative classification for local documentation-test commands."""

from pathlib import Path
import re


SHELLS = {"bash", "cmd", "fish", "powershell", "pwsh", "sh", "zsh"}
PRIVILEGED = {"doas", "sudo", "su"}
DESTRUCTIVE = {"dd", "mkfs", "rm", "rmdir", "shutdown", "reboot", "unlink"}
ISOLATION = {"bwrap", "docker", "firejail", "podman", "sandbox-exec"}
DANGEROUS_WORDS = {
    "apply", "clean", "cleanup", "delete", "deploy", "destroy", "portal", "publish",
    "push", "release", "submit", "testflight", "upload", "upgrade",
}
SHELL_MARKERS = ("\n", "\r", "\0", ";", "&&", "||", "|", "`", "$(", ">", "<")
NETWORK_FLAGS = {"network", "allow-net", "allow-network", "online", "internet"}
SECRET_KEYS = {
    "api-key", "apikey", "client-secret", "password", "secret", "token",
}
SOURCE_SUFFIXES = {
    ".bash", ".c", ".cc", ".cpp", ".cs", ".ex", ".exs", ".fish", ".go", ".h",
    ".hpp", ".hrl", ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".m", ".md",
    ".mm", ".php", ".py", ".pyi", ".rb", ".rs", ".rst", ".scala", ".sh", ".swift",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml", ".zsh",
}
FORBIDDEN_COMPONENTS = SHELLS | PRIVILEGED | DESTRUCTIVE | DANGEROUS_WORDS


def classify_command(argv: list[str]) -> str:
    """Classify argv without executing it or interpreting shell syntax."""
    if (type(argv) is not list or not argv or len(argv) > 128 or
            any(type(token) is not str or not token or len(token) > 4096 for token in argv)):
        return "inconclusive"
    if any(any(marker in token for marker in SHELL_MARKERS) for token in argv):
        return "refused"

    executable = _name(argv[0])
    if (_has_forbidden_component(executable) or
            any(not _is_source_path(token) and _has_forbidden_component(token)
                for token in argv[1:])):
        return "refused"
    if executable in ("timeout", "gtimeout"):
        if len(argv) < 3 or not argv[1].isascii() or not argv[1].isdecimal():
            return "inconclusive"
        try:
            seconds = int(argv[1])
        except (ValueError, OverflowError):
            return "inconclusive"
        if not 1 <= seconds <= 900:
            return "inconclusive"
        return classify_command(argv[2:])
    if _dangerous_command(executable, argv[1:]):
        return "refused"
    if executable in ISOLATION or _needs_declaration(argv):
        return "inconclusive"
    if _known_test_command(executable, argv[1:]):
        return "allowed"
    return "inconclusive"


def _known_test_command(executable, arguments):
    if executable in {"pytest", "py.test", "tox", "nox", "ctest", "rspec"}:
        return True
    if executable.startswith("python"):
        return len(arguments) >= 2 and arguments[0] == "-m" and arguments[1] in {
            "pytest", "unittest",
        }
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        return bool(arguments) and (
            arguments[0] == "test" or
            len(arguments) >= 2 and arguments[0] == "run" and arguments[1].startswith("test")
        )
    if executable in {"cargo", "go", "swift", "dotnet", "mix"}:
        return bool(arguments) and arguments[0] == "test"
    if executable in {"make", "gradle", "gradlew", "mvn", "mvnw"}:
        return bool(arguments) and arguments[0] == "test"
    if executable == "xcodebuild":
        return "test" in arguments
    if executable == "bundle":
        return len(arguments) >= 2 and arguments[:2] == ["exec", "rspec"]
    return False


def _dangerous_command(executable, arguments):
    first = _word(arguments[0]) if arguments else ""
    if executable == "git" and first in {"clean", "push", "reset"}:
        return True
    if executable in {"npm", "pnpm", "yarn", "bun", "cargo"} and first == "publish":
        return True
    if executable in {"twine"} and first == "upload":
        return True
    if executable in {"docker", "podman"} and first == "push":
        return True
    if executable == "kubectl" and first in {"apply", "create", "delete", "patch", "replace"}:
        return True
    if executable in {"terraform", "pulumi"} and first in {"apply", "destroy", "up"}:
        return True
    if executable == "gh" and first == "release":
        return True
    if executable in {"fastlane", "rm", "rmdir", "unlink"}:
        return True
    if executable == "xcrun" and any(
            _word(token) in {"altool", "notarytool", "submit", "upload"} for token in arguments
    ):
        return True
    if executable in {"make", "gradle", "gradlew", "mvn", "mvnw"} and first == "clean":
        return True
    return False


def _needs_declaration(argv):
    for token in argv[1:]:
        lowered = token.casefold()
        if lowered.startswith(("http://", "https://")):
            return True
        key = lowered.split("=", 1)[0].lstrip("-").replace("_", "-")
        if (key in NETWORK_FLAGS or key in SECRET_KEYS or
                any(key.endswith(f"-{secret}") for secret in SECRET_KEYS)):
            return True
        if "=" in token and any(part in key for part in ("secret", "token", "password", "key")):
            return True
    return _name(argv[0]) == "env"


def _name(token):
    return Path(token).name.casefold()


def _word(token):
    return token.casefold().lstrip("-")


def _operation(token):
    word = _word(token).rsplit(".", 1)[0]
    for separator in (":", "=", "/", "_"):
        word = word.split(separator, 1)[0]
    return word


def _has_forbidden_component(token):
    components = re.findall(r"[a-z0-9]+", token.casefold())
    return any(
        component == word or component.startswith(word) or component.endswith(word)
        for component in components
        for word in FORBIDDEN_COMPONENTS
    )


def _is_source_path(token):
    if token.startswith("-"):
        return False
    path = token.split("::", 1)[0]
    return Path(path).suffix.casefold() in SOURCE_SUFFIXES
