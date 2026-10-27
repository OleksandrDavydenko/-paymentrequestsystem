"""У GitHub Actions невдалий сценарій стає анотацією (::error): її видно в підсумку запуску й через API без входу
в GitHub (журнал кроку без входу не відкривається)."""
import os

_config = None


def pytest_configure(config):
    global _config
    _config = config


def pytest_runtest_logreport(report):
    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed or _config is None:
        return
    crash = getattr(report.longrepr, "reprcrash", None)
    text = crash.message if crash else str(report.longrepr)
    if len(text) > 4000:  # початок — рядки FAIL, кінець — хвіст виводу (traceback)
        text = text[:1500] + "\n…\n" + text[-2500:]
    text = text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    title = report.nodeid.split("::")[-1].replace(",", " ").replace("::", " ")
    tr = _config.pluginmanager.get_plugin("terminalreporter")
    line = f"::error title={title}::{text}"
    if tr:  # команда GitHub розпізнається лише з початку рядка — не в одному рядку з прогресом «..F»
        tr.write("\n")  # ensure_newline не знає про символи прогресу, які пише xdist
        tr.write_line(line)
    else:
        print("\n" + line, flush=True)
