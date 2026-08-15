import json
import shutil
from pathlib import Path

from rich import print

# -- Configuration -------------------------------

SELMA_CONFIG_CONTENT = {
    "agent": {
        "id": "main",
        "name": "Selma",
        "toolsAllow": "all",
    },
    "model": {
        "model": "ollama/llama3.1",
        "thinking": "low",
        "timeout_seconds": 120,
        "ollama_base_url": "http://localhost:11434/v1",
    },
    "session": {
        "reset": {
            "at_hour": 4,
        }
    },
    "channels": {
        "telegram": {
            "enabled": False
            # Token not here — comes from TELEGRAM_TOKEN in .env
        },
        "webchat": {"enabled": False, "host": "0.0.0.0", "port": 8000, "log_level": "warning"},
    },
    "heartbeat": {
        "every": "0m",
        "target": "none",
        "light_context": False,
        "isolated_session": False,
        "ack_max_chars": 300,
    },
    "memory": {
        "vector_search": False,
        "embed_model": "nomic-embed-text",
        "temporal_decay": False,
        "temporal_decay_rate": 0.01,
    },
}

# -- Functions -------------------------------


def setup(agent_base_dir="."):
    """
    Initializes Selma's directory structure, configuration, and templates.
    """
    base_path = Path(agent_base_dir).resolve()
    selma_dir = base_path / ".selma"
    json_path = selma_dir / "selma.json"
    workspace_dir = selma_dir / "workspace"
    template_dir = base_path / "templates"
    skills_src_dir = base_path / "skills"
    skills_dst_dir = workspace_dir / "skills"

    print(f"[bold blue]Initializing Selma Environment[/bold blue]\n[dim]Root: {base_path}[/dim]")

    try:
        # 1. Create .selma directory
        if not selma_dir.exists():
            selma_dir.mkdir(parents=True)
            print(f"[green]✔[/green] Created directory: [cyan]{selma_dir.name}[/cyan]")
        else:
            print(f"[yellow]![/yellow] Directory already exists: [cyan]{selma_dir.name}[/cyan]")

        # 2. Create selma.json file
        if not json_path.exists():
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(SELMA_CONFIG_CONTENT, f, indent=4)
            print(f"[green]✔[/green] Created config: [cyan]{json_path.name}[/cyan]")
        else:
            print(f"[yellow]![/yellow] Config already exists: [cyan]{json_path.name}[/cyan]")

        # 3. Create workspace subdirectory
        if not workspace_dir.exists():
            workspace_dir.mkdir(parents=True)
            print(f"[green]✔[/green] Created workspace: [cyan]{workspace_dir.name}[/cyan]")

        # 3a. Create memory subdirectory + empty MEMORY.md
        memory_dir = workspace_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_index = memory_dir / "MEMORY.md"
        if not memory_index.exists():
            memory_index.write_text("# Memory\n", encoding="utf-8")
            print("[green]✔[/green] Created memory index: [cyan]memory/MEMORY.md[/cyan]")
        else:
            print("[yellow]![/yellow] Memory index already exists: [cyan]memory/MEMORY.md[/cyan]")

        # 4. Handle Template copying
        handle_templates(template_dir, workspace_dir)

        # 5. Copy skills into workspace
        handle_skills(skills_src_dir, skills_dst_dir)

        print("\n[bold green]Setup completed successfully.[/bold green]")

    except Exception as e:
        print(f"[bold red]An error occurred during setup:[/bold red] {e}")


def handle_templates(template_dir: Path, target_dir: Path):
    """
    Checks if templates need to be copied to the workspace.
    Copies only if the target files do not exist.
    """
    if not template_dir.exists():
        print(f"[yellow]⚠[/yellow] Template source [dim]({template_dir})[/dim] not found. Skipping copy.")
        return

    # Get list of files in template directory
    template_files = [f for f in template_dir.iterdir() if f.is_file()]

    if not template_files:
        print("[yellow]⚠[/yellow] Template directory is empty.")
        return

    # Check if ANY of the template files already exist in the target
    files_already_present = any((target_dir / f.name).exists() for f in template_files)

    if not files_already_present:
        print(f"[yellow]i[/yellow] Workspace is empty. Copying [bold]{len(template_files)}[/bold] templates...")
        for file in template_files:
            shutil.copy2(file, target_dir / file.name)
            print(f"  [blue]→[/blue] Copied: {file.name}")
        print("[green]✔ Templates deployed.[/green]")
    else:
        print(
            "[yellow]![/yellow] Workspace already contains template files. [dim]Skipping copy to prevent overwriting.[/dim]"
        )


def handle_skills(skills_src: Path, skills_dst: Path):
    """
    Syncs skills from skills/<name>/ → workspace/skills/<name>/.
    - Removes stale skill directories no longer present in source.
    - Always updates SKILL.md (the skill spec).
    - Skips other files that already exist (no overwrite of user content).
    """
    if not skills_src.exists():
        print(f"[yellow]⚠[/yellow] Skills source [dim]({skills_src})[/dim] not found. Skipping.")
        return

    skill_dirs = [d for d in skills_src.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
    if not skill_dirs:
        print("[yellow]⚠[/yellow] No skills found in skills/ directory.")
        return

    # Remove stale skill directories in workspace that no longer exist in source
    src_skill_names = {d.name for d in skill_dirs}
    if skills_dst.exists():
        for dst_dir in sorted(skills_dst.iterdir()):
            if dst_dir.is_dir() and dst_dir.name not in src_skill_names:
                shutil.rmtree(dst_dir)
                print(f"  [red]✗[/red] Removed stale skill: [cyan]{dst_dir.name}[/cyan]")

    copied_skills = 0
    skipped_skills = 0
    for src_skill_dir in sorted(skill_dirs):
        dst_skill_dir = skills_dst / src_skill_dir.name
        dst_skill_dir.mkdir(parents=True, exist_ok=True)

        files = [f for f in src_skill_dir.iterdir() if f.is_file()]
        # SKILL.md is always synced; other files only if not yet present
        files_to_copy = [f for f in files if f.name == "SKILL.md" or not (dst_skill_dir / f.name).exists()]

        if not files_to_copy:
            skipped_skills += 1
            continue

        for f in files_to_copy:
            shutil.copy2(f, dst_skill_dir / f.name)

        print(f"  [blue]→[/blue] Skill synced: {src_skill_dir.name} ({len(files_to_copy)} file(s))")
        copied_skills += 1

    if copied_skills:
        print(f"[green]✔[/green] {copied_skills} skill(s) synced to workspace/skills/")
    elif skipped_skills:
        print("[yellow]![/yellow] All skills up to date in workspace. [dim]Skipping.[/dim]")


if __name__ == "__main__":
    # Run the setup in the current directory by default
    setup()
