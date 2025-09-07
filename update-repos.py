#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "rich",
# ]
# ///

"""
Diode PCB Workflow Updater

Updates pcb-release.yml workflow across dioderobot repositories.

Usage:
    ./update-repos.py                    # Process top 3 repos updated in last 30 days
    ./update-repos.py --limit 10         # Process top 10 repos
    ./update-repos.py --days 7           # Look at repos updated in last 7 days
    ./update-repos.py --work-dir ./tmp   # Use different work directory
    ./update-repos.py --status-only      # Just check existing PR status
    ./update-repos.py --merge-passing    # Auto-merge PRs with all checks passed
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.progress import Progress

console = Console()

# Repositories to exclude from processing
EXCLUDED_REPOS = {"stdlib", "diodelib", "elodin", "legacy-demo", "customer"}
OWNER = "dioderobot"
BRANCH_NAME = "update-workflow-pcb-release"
WORKFLOW_RELATIVE = ".github/workflows/pcb-release.yml"

# ==============================================================================
# Core Data Classes and Utilities
# ==============================================================================

@dataclass
class PRData:
    """Structured PR data with helpers."""
    repo_name: str
    url: str
    state: str
    mergeable: str
    checks: List[Dict]

    @property
    def is_open(self) -> bool:
        return self.state.upper() == "OPEN"

    @property
    def is_mergeable(self) -> bool:
        return self.mergeable.upper() == "MERGEABLE"

    @property
    def check_summary(self) -> Tuple[int, int, int]:
        passed = failed = pending = 0
        for check in self.checks or []:
            status = (check.get("status") or "").upper()
            conclusion_val: Optional[str] = check.get("conclusion")
            conclusion = conclusion_val.upper() if conclusion_val else None
            if status in ["QUEUED", "IN_PROGRESS"] or conclusion is None:
                pending += 1
            elif conclusion == "SUCCESS":
                passed += 1
            elif conclusion in ["FAILURE", "ERROR"]:
                failed += 1
            else:
                pending += 1
        return passed, failed, pending

    @property
    def can_merge(self) -> bool:
        """True if PR can be safely auto-merged."""
        if not self.is_open or not self.is_mergeable:
            return False
        passed, failed, pending = self.check_summary
        return failed == 0 and pending == 0 and passed > 0


# ==============================================================================
# Subprocess helpers
# ==============================================================================

def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run a command and return the CompletedProcess with captured output."""
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check, env=env)


def git(repo_path: Path, *args: str, check: bool = True, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo_path), *args], check=check, env=env)


def gh_json(args: List[str]) -> Dict:
    """Run a gh command that returns JSON and parse it."""
    cp = run(["gh", *args])
    return json.loads(cp.stdout or "{}")

def fetch_pr_data(repo_name: str, pr_url: str) -> PRData:
    """Fetch detailed PR data for a repository."""
    pr_url = pr_url.split(' ')[0]
    data = gh_json([
        "pr", "view", pr_url,
        "--json", "title,state,mergeable,statusCheckRollup",
    ])
    return PRData(
        repo_name=repo_name,
        url=pr_url,
        state=data.get('state', 'UNKNOWN'),
        mergeable=data.get('mergeable', 'UNKNOWN'),
        checks=data.get('statusCheckRollup', [])
    )

# ==============================================================================
# Repository Operations
# ==============================================================================

def get_recent_repos(owner: str, days: int, limit: int) -> List[Dict]:
    """Get recently updated repositories."""
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_iso = cutoff_date.isoformat() + "Z"
    
    console.print(f"[blue]Fetching {owner} repositories updated since:[/blue] {cutoff_iso}")
    
    repos = gh_json([
        "repo", "list", owner,
        "--limit", "100",
        "--json", "name,nameWithOwner,updatedAt,description,url,primaryLanguage",
    ])
    items = repos if isinstance(repos, list) else []

    # Filter excluded and by cutoff
    def parse_dt(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    filtered = []
    for repo in items:
        if repo.get("name") in EXCLUDED_REPOS:
            continue
        try:
            updated_at = parse_dt(repo["updatedAt"])  # type: ignore[index]
        except Exception:
            continue
        if updated_at >= cutoff_date.replace(tzinfo=updated_at.tzinfo):
            filtered.append(repo)

    # Sort by updated desc and slice to limit
    filtered.sort(key=lambda r: r.get("updatedAt", ""), reverse=True)
    return filtered[:limit]

def clone_or_update_repo(repo: Dict, work_dir: Path) -> Tuple[bool, str]:
    """Clone or update repository to latest main."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    repo_url = f"https://github.com/{repo['nameWithOwner']}.git"
    
    try:
        if repo_path.exists():
            git(repo_path, 'fetch', 'origin')
            git(repo_path, 'checkout', 'main')
            git(repo_path, 'reset', '--hard', 'origin/main')
            return True, "updated"
        else:
            run(['git', 'clone', repo_url, str(repo_path)])
            return True, "cloned"
    except subprocess.CalledProcessError as e:
        return False, f"failed: {e}"

def update_workflow_file(repo: Dict, work_dir: Path, source_workflow: Path) -> Tuple[bool, str, bool]:
    """Update workflow file and commit if changed."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    target_workflow = repo_path / WORKFLOW_RELATIVE
    
    try:
        # Check if workflow already matches (contents)
        if target_workflow.exists():
            try:
                if source_workflow.read_bytes() == target_workflow.read_bytes():
                    return True, "workflow up to date", False
            except FileNotFoundError:
                pass
        
        # Create branch for changes
        git(repo_path, 'checkout', 'main')
        git(repo_path, 'branch', '-D', BRANCH_NAME, check=False)
        git(repo_path, 'checkout', '-b', BRANCH_NAME)
        
        # Update workflow file
        target_workflow.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_workflow, target_workflow)
        git(repo_path, 'add', WORKFLOW_RELATIVE)
        
        # Check if there are staged changes
        result = git(repo_path, 'diff', '--name-only', '--cached')
        
        if (result.stdout or '').strip():
            # Commit changes with Diode Robot as author and committer
            git_env = {
                **os.environ,
                'GIT_AUTHOR_NAME': 'Diode Robot',
                'GIT_AUTHOR_EMAIL': 'info@diode.run',
                'GIT_COMMITTER_NAME': 'Diode Robot', 
                'GIT_COMMITTER_EMAIL': 'info@diode.run'
            }
            
            git(repo_path, 'commit', '-m', 'Update GitHub workflow: pcb-release.yml', check=True, env=git_env)
            return True, "workflow updated & committed", True
        else:
            # No changes, clean up branch
            git(repo_path, 'checkout', 'main')
            git(repo_path, 'branch', '-d', BRANCH_NAME)
            return True, "workflow up to date", False
            
    except subprocess.CalledProcessError as e:
        return False, f"workflow failed: {e}", False

def create_or_update_pr(repo: Dict, work_dir: Path) -> Tuple[bool, str]:
    """Create or update PR for workflow changes."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    
    try:
        # Check if local branch differs from remote
        try:
            diff_result = git(repo_path, 'diff', f'origin/{BRANCH_NAME}', BRANCH_NAME)
            has_changes = bool((diff_result.stdout or '').strip())
        except subprocess.CalledProcessError:
            has_changes = True  # Remote branch doesn't exist
        
        if has_changes:
            git(repo_path, 'push', '--force-with-lease', 'origin', BRANCH_NAME)
        
        # Check if PR already exists
        existing_prs = gh_json([
            'pr', 'list', '--repo', f"{OWNER}/{repo_name}", '--head', BRANCH_NAME, '--json', 'url'
        ])
        if isinstance(existing_prs, list) and existing_prs:
            pr_url = existing_prs[0]['url']
            return True, pr_url
        
        # Create new PR
        result = run([
            'gh', 'pr', 'create', '--repo', f"{OWNER}/{repo_name}",
            '--base', 'main', '--head', BRANCH_NAME,
            '--title', 'Update GitHub workflow: pcb-release.yml',
            '--body', 'This PR updates the pcb-release.yml workflow file to the latest version.'
        ], cwd=repo_path)
        return True, (result.stdout or '').strip()
        
    except subprocess.CalledProcessError as e:
        error_msg = str(e.stderr) if hasattr(e, 'stderr') and e.stderr else str(e)
        return False, f"PR creation failed: {error_msg}"

# ==============================================================================
# PR Management
# ==============================================================================

def find_workflow_prs(owner: str) -> List[Tuple[str, str]]:
    """Find all existing open workflow PRs across repositories via gh search."""
    try:
        items = gh_json([
            "search", "prs", "--owner", owner,
            "--head", BRANCH_NAME, "--state", "open",
            "--json", "url,repository"
        ])
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error searching PRs: {e}[/red]")
        return []

    results: List[Tuple[str, str]] = []
    if not isinstance(items, list):
        return results

    for it in items:
        repo_info = it.get("repository") or {}
        # Try several shapes: name, nameWithOwner
        repo_name = repo_info.get("name") or (
            (repo_info.get("nameWithOwner") or "").split("/")[-1]
        )
        if not repo_name or repo_name in EXCLUDED_REPOS:
            continue
        url = it.get("url")
        if url:
            results.append((repo_name, url))

    return results

def display_pr_status_table(pr_list: List[Tuple[str, str]]):
    """Display PR status in a table format."""
    if not pr_list:
        console.print("[dim]No workflow PRs found[/dim]")
        return
    
    table = Table(show_header=True, header_style="bold")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("PR URL", style="blue", no_wrap=True)
    table.add_column("Checks", style="yellow")
    
    for repo_name, pr_url in pr_list:
        try:
            pr_data = fetch_pr_data(repo_name, pr_url)
            
            # Only show open PRs
            if not pr_data.is_open:
                continue
                
            # Format check status
            passed, failed, pending = pr_data.check_summary
            if not pr_data.checks:
                check_display = "[dim]No checks[/dim]"
            else:
                parts = []
                if failed > 0:
                    parts.append(f"[red]{failed} failed[/red]")
                if passed > 0:
                    parts.append(f"[green]{passed} passed[/green]")
                if pending > 0:
                    parts.append(f"[yellow]{pending} pending[/yellow]")
                check_display = ", ".join(parts) if parts else "[dim]Unknown[/dim]"
            
            table.add_row(repo_name, pr_data.url, check_display)
            
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            table.add_row(repo_name, pr_url.split(' ')[0], "[red]Error fetching status[/red]")
    
    console.print(table)

def merge_passing_prs(pr_list: List[Tuple[str, str]]) -> int:
    """Merge PRs that have all checks passed."""
    merged_count = 0
    
    for repo_name, pr_url in pr_list:
        try:
            pr_data = fetch_pr_data(repo_name, pr_url)
            
            if pr_data.can_merge:
                run([
                    'gh', 'pr', 'merge', pr_data.url,
                    '--squash', '--auto'
                ])
                
                console.print(f"[green]✓[/green] Merged PR for {repo_name}")
                merged_count += 1
                
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            console.print(f"[red]✗[/red] Failed to merge PR for {repo_name}: {e}")
    
    return merged_count

# ==============================================================================
# Main Workflow
# ==============================================================================

def display_repos_table(repos: List[Dict], days: int):
    """Display repositories in a table."""
    if not repos:
        console.print(f"No repositories updated in the last {days} days.")
        return
    
    table = Table(show_header=True, header_style="bold")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Updated", style="green")
    table.add_column("Language", style="blue")
    table.add_column("Description", style="dim")
    
    for repo in repos:
        updated_at = datetime.fromisoformat(repo['updatedAt'].replace('Z', '+00:00'))
        time_ago = datetime.now(updated_at.tzinfo) - updated_at
        
        if time_ago.days == 0:
            time_str = f"{time_ago.seconds // 3600}h ago" if time_ago.seconds >= 3600 else f"{time_ago.seconds // 60}m ago"
        else:
            time_str = f"{time_ago.days}d ago"
        
        description = repo.get('description', 'No description') or 'No description'
        if len(description) > 60:
            description = description[:57] + "..."
        
        language = repo.get('primaryLanguage', {})
        lang_name = language.get('name', 'N/A') if language else 'N/A'
        
        table.add_row(repo['nameWithOwner'], time_str, lang_name, description)
    
    console.print(table)

def process_repositories(repos: List[Dict], work_dir: Path, source_workflow: Path) -> List[Tuple[str, str]]:
    """Process repositories and return list of created/updated PRs."""
    work_dir.mkdir(exist_ok=True)
    console.print(f"\n[bold]Setting up repositories in {work_dir}[/bold]")
    
    created_prs = []
    
    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("Processing repositories", total=len(repos))
        
        for repo in repos:
            repo_name = repo['name']
            progress.update(task, description=f"Processing {repo_name}")
            
            # Clone/update repo
            clone_success, clone_status = clone_or_update_repo(repo, work_dir)
            if not clone_success:
                console.print(f"[red]✗[/red] {repo_name}: {clone_status}")
                progress.advance(task)
                continue
            
            # Update workflow
            workflow_success, workflow_status, has_changes = update_workflow_file(repo, work_dir, source_workflow)
            
            # Status message
            icon = "+" if clone_status == "cloned" else "→" if has_changes else "✓"
            color = "blue" if has_changes or clone_status == "cloned" else "green"
            
            status_parts = [clone_status]
            if workflow_status != "workflow up to date":
                status_parts.append(workflow_status)
            
            console.print(f"[{color}]{icon}[/{color}] {repo_name}: {', '.join(status_parts)}")
            
            # Create PR if needed
            if has_changes:
                pr_success, pr_url = create_or_update_pr(repo, work_dir)
                if pr_success and pr_url:
                    console.print(f"[green]→[/green] PR for {repo_name}: {pr_url}")
                    created_prs.append((repo_name, pr_url))
                else:
                    console.print(f"[red]✗[/red] Failed to create PR for {repo_name}")
            
            progress.advance(task)
    
    console.print(f"\n[green]Processed {len(repos)} repositories successfully[/green]")
    return created_prs

def status_only_mode(owner: str, merge_passing: bool):
    """Check status of existing workflow PRs without updating repos."""
    console.print("[blue]Finding existing workflow PRs...[/blue]")
    existing_prs = find_workflow_prs(owner)
    
    if existing_prs:
        console.print(f"\n[bold]Found {len(existing_prs)} workflow PRs:[/bold]")
        display_pr_status_table(existing_prs)
        
        if merge_passing:
            console.print("\n[bold]Auto-merging PRs with passing checks...[/bold]")
            merged_count = merge_passing_prs(existing_prs)
            if merged_count > 0:
                console.print(f"[green]Auto-merged {merged_count} PRs[/green]")
            else:
                console.print("[dim]No PRs were auto-merged[/dim]")
    else:
        console.print("[dim]No workflow PRs found[/dim]")

def full_update_mode(owner: str, args):
    """Full repository update mode."""
    repos = get_recent_repos(owner, args.days, args.limit)
    display_repos_table(repos, args.days)
    
    # Check source workflow exists
    source_workflow = Path.cwd() / WORKFLOW_RELATIVE
    if not source_workflow.exists():
        console.print(f"[red]Source workflow file not found: {source_workflow}[/red]")
        sys.exit(1)
    
    # Process repositories
    created_prs = process_repositories(repos, args.work_dir, source_workflow)
    
    if created_prs:
        console.print(f"\n[bold]Created {len(created_prs)} pull requests:[/bold]")
        for repo_name, pr_url in created_prs:
            console.print(f"  • {repo_name}: {pr_url}")
        
        console.print("\n[bold]PR Status Summary:[/bold]")
        display_pr_status_table(created_prs)
        
        if args.merge_passing:
            console.print("\n[bold]Auto-merging PRs with passing checks...[/bold]")
            merged_count = merge_passing_prs(created_prs)
            if merged_count > 0:
                console.print(f"[green]Auto-merged {merged_count} PRs[/green]")
            else:
                console.print("[dim]No PRs were auto-merged[/dim]")
    else:
        console.print("\n[dim]No pull requests were created (no changes needed)[/dim]")

# ==============================================================================
# Main Entry Point
# ==============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Update PCB workflow across repositories")
    parser.add_argument("--limit", type=int, default=3, help="Number of repositories to process (default: 3)")
    parser.add_argument("--days", type=int, default=30, help="Number of days to look back (default: 30)")
    parser.add_argument("--work-dir", type=Path, default=Path("work"), help="Work directory for repos (default: work/)")
    parser.add_argument("--merge-passing", action="store_true", help="Auto-merge PRs that have all checks passed")
    parser.add_argument("--status-only", action="store_true", help="Only check PR status without cloning/updating repos")
    parser.add_argument("--owner", type=str, default=OWNER, help=f"GitHub org/owner (default: {OWNER})")
    
    args = parser.parse_args()
    
    try:
        if args.status_only:
            status_only_mode(args.owner, args.merge_passing)
        else:
            full_update_mode(args.owner, args)
    except KeyboardInterrupt:
        console.print("\n[red]Cancelled by user[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
