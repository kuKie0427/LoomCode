import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# 边界标记 — 用它来分割缓存段和非缓存段
BOUNDARY = "\n\n---\n\n"

@dataclass
class SystemPrompt:
    static: list[str] = field(default_factory=list)
    session: list[str] = field(default_factory=list)
    memory: list[str] = field(default_factory=list)

    def add_static(self, section: str) -> None:
        self.static.append(section+"\n")

    def add_session(self, section: str) -> None:
        self.session.append(section+"\n")

    def add_memory(self, section: str) -> None:
        self.memory.append(section+"\n")

    def add_dynamic(self, section: str) -> None:
        self.session.append(section+"\n")

    def build(self) -> str:
        out: list[str] = list(self.static)
        if self.session:
            out.append(BOUNDARY)
            out.extend(self.session)
        if self.memory:
            out.append(BOUNDARY)
            out.extend(self.memory)
        return "".join(out)

    def get_git_context(self, repo_path: Path) -> str:
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            log = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            parts = []
            if branch.returncode == 0 and branch.stdout.strip():
                parts.append(f"当前分支: {branch.stdout.strip()}")
            if log.returncode == 0 and log.stdout.strip():
                parts.append(f"最近提交:\n{log.stdout.strip()}")
            return "Git 上下文:\n" + "\n".join(parts) if parts else "Git 上下文: 无 (not a git repository)"
        except Exception as e:
            return f"Git 上下文获取失败: {e}"

if __name__ == "__main__":
    sp = SystemPrompt()
    WORKDIR1 = Path.cwd()
    WORKDIR2 = Path("/Users/lanf/minio")
    sp.add_static("你是一个编程主智能体，协助我进行开发任务。")
    sp.add_static("行为准则：小心操作，不破坏系统，不泄露数据。")
    sp.add_static("语言风格：简洁、直接、无废话。")
    sp.add_session(f"工作目录: {WORKDIR1}")
    sp.add_session(sp.get_git_context(WORKDIR1))
    logger.info(sp.build())