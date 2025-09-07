#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "rich",
# ]
# ///

"""
Diode PCB Workflow Updater

This script automatically updates the pcb-release.yml workflow file across
multiple dioderobot repositories. It:

1. Fetches recently updated repositories from the dioderobot GitHub organization
2. Clones/updates each repo to the latest main branch
3. Updates the .github/workflows/pcb-release.yml file if it differs from the source
4. Creates pull requests for any changes made
5. Reports the status of all created PRs including CI check results

Usage:
    ./update-repos.py                    # Process top 3 repos updated in last 30 days
    ./update-repos.py --limit 10         # Process top 10 repos
    ./update-repos.py --days 7           # Look at repos updated in last 7 days
    ./update-repos.py --work-dir ./tmp   # Use different work directory

The script will create a "work/" directory (or specified directory) containing
cloned repositories. Existing repos are reset to origin/main for clean state.

Pull requests use the branch name "update-workflow-pcb-release" and are authored
by "Diode Robot <info@diode.run>".
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict

from rich.console import Console
from rich.table import Table
from rich.progress import Progress

console = Console()

# Repositories to exclude from processing
EXCLUDED_REPOS = {
    "stdlib", "diodelib", "legacy-demo", "customer"
}

def get_repos_updated_since_days(owner: str, days: int = 30, limit: int = 30) -> List[Dict]:
    """Fetch repositories updated in the last N days using GitHub CLI."""
    
    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_iso = cutoff_date.isoformat() + "Z"
    
    console.print(f"[blue]Fetching {owner} repositories updated since:[/blue] {cutoff_iso}")
    
    try:
        # Run gh command
        result = subprocess.run([
            "gh", "repo", "list", owner,
            "--limit", "100",  # Fetch plenty to account for excluded repos
            "--json", "name,nameWithOwner,updatedAt,description,url,primaryLanguage"
        ], capture_output=True, text=True, check=True)
        
        repos = json.loads(result.stdout)
        
        # Filter repos updated since cutoff date and not excluded
        filtered_repos = []
        for repo in repos:
            # Skip excluded repos
            if repo['name'] in EXCLUDED_REPOS:
                continue
                
            updated_at = datetime.fromisoformat(repo['updatedAt'].replace('Z', '+00:00'))
            if updated_at >= cutoff_date.replace(tzinfo=updated_at.tzinfo):
                filtered_repos.append(repo)
                # Stop once we have enough repos
                if len(filtered_repos) >= limit:
                    break
        
        # Sort by update time (newest first) - already sorted from gh but just in case
        filtered_repos.sort(key=lambda x: x['updatedAt'], reverse=True)
        
        return filtered_repos
        
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error running gh command:[/red] {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing JSON:[/red] {e}")
        sys.exit(1)

def display_repos(repos: List[Dict], days: int):
    """Display repositories in a table."""
    
    if not repos:
        console.print(f"No repositories updated in the last {days} days.")
        return
    
    # Create table
    table = Table(show_header=True, header_style="bold")
    
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Updated", style="green")
    table.add_column("Language", style="blue")
    table.add_column("Description", style="dim")
    
    for repo in repos:
        # Format update time
        updated_at = datetime.fromisoformat(repo['updatedAt'].replace('Z', '+00:00'))
        time_ago = datetime.now(updated_at.tzinfo) - updated_at
        
        if time_ago.days == 0:
            time_str = f"{time_ago.seconds // 3600}h ago" if time_ago.seconds >= 3600 else f"{time_ago.seconds // 60}m ago"
        else:
            time_str = f"{time_ago.days}d ago"
        
        # Truncate description
        description = repo.get('description', 'No description') or 'No description'
        if len(description) > 60:
            description = description[:57] + "..."
        
        table.add_row(
            repo['nameWithOwner'],
            time_str,
            repo.get('primaryLanguage', {}).get('name', 'N/A') if repo.get('primaryLanguage') else 'N/A',
            description
        )
    
    console.print(table)

def clone_or_update_repo(repo: Dict, work_dir: Path) -> tuple[bool, str]:
    """Clone a repository or update it to latest main if it already exists."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    repo_url = f"https://github.com/{repo['nameWithOwner']}.git"
    
    try:
        if repo_path.exists():
            # Always reset to origin/main for clean state
            subprocess.run(['git', '-C', str(repo_path), 'fetch', 'origin'], 
                         capture_output=True, check=True)
            subprocess.run(['git', '-C', str(repo_path), 'checkout', 'main'], 
                         capture_output=True, check=True)
            subprocess.run(['git', '-C', str(repo_path), 'reset', '--hard', 'origin/main'], 
                         capture_output=True, check=True)
            return True, "updated"
        else:
            subprocess.run(['git', 'clone', repo_url, str(repo_path)], 
                         capture_output=True, check=True)
            return True, "cloned"
    except subprocess.CalledProcessError as e:
        return False, f"failed: {e}"

def update_workflow(repo: Dict, work_dir: Path, source_workflow: Path) -> tuple[bool, str, bool]:
    """Update the pcb-release.yml workflow file if it differs from source."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    target_workflow_dir = repo_path / '.github' / 'workflows'
    target_workflow = target_workflow_dir / 'pcb-release.yml'
    branch_name = "update-workflow-pcb-release"
    
    try:
        # Create .github/workflows directory if it doesn't exist
        target_workflow_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if workflow file exists and differs from source
        if target_workflow.exists():
            # Compare files
            result = subprocess.run(['diff', str(source_workflow), str(target_workflow)], 
                                  capture_output=True)
            if result.returncode == 0:
                return True, "workflow up to date", False
        
        # Ensure we're on main first, then delete existing branch if it exists
        subprocess.run(['git', '-C', str(repo_path), 'checkout', 'main'], 
                     capture_output=True, check=True)
        subprocess.run(['git', '-C', str(repo_path), 'branch', '-D', branch_name], 
                     capture_output=True)  # Don't check=True, branch might not exist
        subprocess.run(['git', '-C', str(repo_path), 'checkout', '-b', branch_name], 
                     capture_output=True, check=True)
        
        # Copy the workflow file
        shutil.copy2(source_workflow, target_workflow)
        
        # Stage the file first, then check if there are changes to commit
        subprocess.run(['git', '-C', str(repo_path), 'add', '.github/workflows/pcb-release.yml'], 
                     capture_output=True, check=True)
        
        # Check if there are staged changes
        result = subprocess.run(['git', '-C', str(repo_path), 'diff', '--name-only', '--cached'], 
                              capture_output=True, text=True)
        
        if result.stdout.strip():
            # Commit the staged changes on the feature branch
            
            # Set environment variables for both author and committer
            import os
            git_env = {
                **os.environ,
                'GIT_AUTHOR_NAME': 'Diode Robot',
                'GIT_AUTHOR_EMAIL': 'info@diode.run',
                'GIT_COMMITTER_NAME': 'Diode Robot', 
                'GIT_COMMITTER_EMAIL': 'info@diode.run'
            }
            
            subprocess.run(['git', '-C', str(repo_path), 'commit', 
                          '-m', 'Update GitHub workflow: pcb-release.yml'], 
                         check=True, capture_output=True, env=git_env)
            return True, "workflow updated & committed", True
        else:
            # Switch back to main if no changes
            subprocess.run(['git', '-C', str(repo_path), 'checkout', 'main'], 
                         capture_output=True, check=True)
            subprocess.run(['git', '-C', str(repo_path), 'branch', '-d', branch_name], 
                         capture_output=True, check=True)
            return True, "workflow up to date", False
        
    except subprocess.CalledProcessError as e:
        return False, f"workflow failed: {e}", False
    except Exception as e:
        return False, f"workflow error: {e}", False

def create_pr_for_changes(repo: Dict, work_dir: Path) -> tuple[bool, str]:
    """Create a PR for the workflow changes if any commits were made."""
    repo_name = repo['name']
    repo_path = work_dir / repo_name
    branch_name = "update-workflow-pcb-release"
    
    try:
        # Check if local branch differs from remote branch
        try:
            diff_result = subprocess.run(['git', '-C', str(repo_path), 'diff', f'origin/{branch_name}', branch_name], 
                                       capture_output=True, text=True, check=True)
            has_changes = bool(diff_result.stdout.strip())
        except subprocess.CalledProcessError:
            # Remote branch doesn't exist or other error, assume we need to push
            has_changes = True
        
        if has_changes:
            # Force push the branch to origin (overwrites existing branch if it exists)
            subprocess.run(['git', '-C', str(repo_path), 'push', '--force-with-lease', 'origin', branch_name], 
                         capture_output=True, check=True)
        
        # Check if PR already exists first
        check_result = subprocess.run([
            'gh', 'pr', 'list',
            '--repo', f"dioderobot/{repo_name}",
            '--head', branch_name,
            '--json', 'url'
        ], capture_output=True, text=True, check=True, cwd=str(repo_path))
        
        existing_prs = json.loads(check_result.stdout)
        if existing_prs:
            # PR already exists, return the existing URL
            pr_url = existing_prs[0]['url']
            status_msg = "(updated existing PR)" if has_changes else "(existing PR, no changes)"
            return True, f"{pr_url} {status_msg}"
        
        # Create new PR using gh CLI
        result = subprocess.run([
            'gh', 'pr', 'create',
            '--repo', f"dioderobot/{repo_name}",
            '--base', 'main',
            '--head', branch_name,
            '--title', 'Update GitHub workflow: pcb-release.yml',
            '--body', 'This PR updates the pcb-release.yml workflow file to the latest version.'
        ], capture_output=True, text=True, check=True, cwd=str(repo_path))
        
        # Extract PR URL from output
        pr_url = result.stdout.strip()
        return True, pr_url
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr and hasattr(e.stderr, 'decode') else str(e.stderr) if e.stderr else str(e)
        return False, f"PR creation failed: {error_msg}"

def check_pr_statuses(created_prs: List[tuple], work_dir: Path):
    """Check and display the status of PRs including CI check results."""
    
    # Create table for PR status
    table = Table(show_header=True, header_style="bold")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("PR URL", style="blue", no_wrap=True)
    table.add_column("Checks", style="yellow")
    
    for repo_name, pr_url in created_prs:
        # Extract PR number and clean URL from the URL string
        clean_url = pr_url.split(' ')[0]  # Remove "(updated existing PR)" suffix
        pr_number = clean_url.split('/')[-1]
        
        try:
            # Get PR details including check status
            result = subprocess.run([
                'gh', 'pr', 'view', pr_number,
                '--repo', f"dioderobot/{repo_name}",
                '--json', 'title,state,mergeable,statusCheckRollup'
            ], capture_output=True, text=True, check=True)
            
            pr_data = json.loads(result.stdout)
            state = pr_data.get('state', 'UNKNOWN')
            checks = pr_data.get('statusCheckRollup', [])
            
            # Skip if PR is not open
            if state.upper() != 'OPEN':
                continue
            
            # Determine overall check status
            if not checks:
                check_display = "[dim]No checks[/dim]"
            else:
                passed = 0
                failed = 0  
                pending = 0
                
                for check in checks:
                    status = check.get('status', '').upper()
                    conclusion = check.get('conclusion', '').upper() if check.get('conclusion') else None
                    
                    if status in ['QUEUED', 'IN_PROGRESS'] or conclusion is None:
                        pending += 1
                    elif conclusion == 'SUCCESS':
                        passed += 1
                    elif conclusion in ['FAILURE', 'ERROR']:
                        failed += 1
                    elif conclusion in ['CANCELLED', 'SKIPPED', 'TIMED_OUT', 'NEUTRAL']:
                        # Count these as "other" but don't show separately for now
                        pending += 1
                
                parts = []
                if failed > 0:
                    parts.append(f"[red]{failed} failed[/red]")
                if passed > 0:
                    parts.append(f"[green]{passed} passed[/green]")
                if pending > 0:
                    parts.append(f"[yellow]{pending} pending[/yellow]")
                
                if parts:
                    check_display = ", ".join(parts)
                else:
                    check_display = "[dim]Unknown[/dim]"
            
            # Add row to table
            table.add_row(repo_name, clean_url, check_display)
            
        except subprocess.CalledProcessError:
            table.add_row(repo_name, clean_url, "[red]Failed to get status[/red]")
        except (json.JSONDecodeError, KeyError):
            table.add_row(repo_name, clean_url, "[red]Parse error[/red]")
    
    console.print(table)

def merge_passing_prs(created_prs: List[tuple]):
    """Merge PRs that have all checks passed and no pending checks."""
    
    merged_count = 0
    
    for repo_name, pr_url in created_prs:
        # Extract PR number and clean URL from the URL string
        clean_url = pr_url.split(' ')[0]  # Remove "(updated existing PR)" suffix
        pr_number = clean_url.split('/')[-1]
        
        try:
            # Get PR details including check status
            result = subprocess.run([
                'gh', 'pr', 'view', pr_number,
                '--repo', f"dioderobot/{repo_name}",
                '--json', 'title,state,mergeable,statusCheckRollup'
            ], capture_output=True, text=True, check=True)
            
            pr_data = json.loads(result.stdout)
            state = pr_data.get('state', 'UNKNOWN')
            mergeable = pr_data.get('mergeable', 'UNKNOWN')
            checks = pr_data.get('statusCheckRollup', [])
            
            # Skip if PR is not open or not mergeable
            if state.upper() != 'OPEN' or mergeable.upper() != 'MERGEABLE':
                continue
            
            # Check if all checks passed (no failures, no pending)
            if not checks:
                # No checks means we can't auto-merge safely
                continue
                
            passed = 0
            failed = 0  
            pending = 0
            
            for check in checks:
                status = check.get('status', '').upper()
                conclusion = check.get('conclusion', '').upper() if check.get('conclusion') else None
                
                if status in ['QUEUED', 'IN_PROGRESS'] or conclusion is None:
                    pending += 1
                elif conclusion == 'SUCCESS':
                    passed += 1
                elif conclusion in ['FAILURE', 'ERROR']:
                    failed += 1
                elif conclusion in ['CANCELLED', 'SKIPPED', 'TIMED_OUT', 'NEUTRAL']:
                    pending += 1
            
            # Only merge if all checks passed and none failed or pending
            if failed == 0 and pending == 0 and passed > 0:
                # Merge the PR
                subprocess.run([
                    'gh', 'pr', 'merge', pr_number,
                    '--repo', f"dioderobot/{repo_name}",
                    '--squash',  # Use squash merge for clean history
                    '--auto'     # Auto-merge when checks pass
                ], capture_output=True, text=True, check=True)
                
                console.print(f"[green]✓[/green] Merged PR for {repo_name}")
                merged_count += 1
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to merge PR for {repo_name}: {e}")
        except (json.JSONDecodeError, KeyError) as e:
            console.print(f"[red]✗[/red] Error processing PR data for {repo_name}: {e}")
    
    if merged_count > 0:
        console.print(f"\n[green]Auto-merged {merged_count} PRs with passing checks[/green]")
    else:
        console.print("\n[dim]No PRs were auto-merged (no PRs with all checks passed)[/dim]")

def setup_repos(repos: List[Dict], work_dir: Path, source_workflow: Path, merge_passing: bool = False):
    """Clone or update all repositories to the work directory and update workflows."""
    work_dir.mkdir(exist_ok=True)
    
    console.print(f"\n[bold]Setting up repositories in {work_dir}[/bold]")
    
    success_count = 0
    created_prs = []
    
    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("Processing repositories", total=len(repos))
        
        for repo in repos:
            repo_name = repo['name']
            progress.update(task, description=f"Processing {repo_name}")
            
            # Clone or update repo
            clone_success, clone_status = clone_or_update_repo(repo, work_dir)
            if not clone_success:
                console.print(f"[red]✗[/red] {repo_name}: {clone_status}")
                progress.advance(task)
                continue
            
            # Update workflow  
            workflow_success, workflow_status, has_changes = update_workflow(repo, work_dir, source_workflow)
            
            # Combined status message
            if clone_status == "cloned":
                icon = "+"
                color = "blue"
            elif workflow_status == "workflow updated & committed":
                icon = "→"
                color = "blue"
            else:
                icon = "✓"
                color = "green"
            
            status_parts = [clone_status]
            if workflow_status != "workflow up to date":
                status_parts.append(workflow_status)
            
            console.print(f"[{color}]{icon}[/{color}] {repo_name}: {', '.join(status_parts)}")
            success_count += 1
            
            # Create PR if there were changes
            if has_changes:
                pr_success, pr_result = create_pr_for_changes(repo, work_dir)
                if pr_success:
                    console.print(f"[green]→[/green] Created PR for {repo_name}: {pr_result}")
                    created_prs.append((repo_name, pr_result))
                else:
                    console.print(f"[red]✗[/red] Failed to create PR for {repo_name}: {pr_result}")
            
            progress.advance(task)
    
    console.print(f"\n[green]Processed {success_count}/{len(repos)} repositories successfully[/green]")
    
    if created_prs:
        console.print(f"\n[bold]Created {len(created_prs)} pull requests:[/bold]")
        for repo_name, pr_url in created_prs:
            console.print(f"  • {repo_name}: {pr_url}")
        
        # Check PR status and CI results
        console.print("\n[bold]PR Status Summary:[/bold]")
        check_pr_statuses(created_prs, work_dir)
        
        # Auto-merge passing PRs if requested
        if merge_passing:
            console.print("\n[bold]Auto-merging PRs with passing checks...[/bold]")
            merge_passing_prs(created_prs)
    else:
        console.print("\n[dim]No pull requests were created (no changes needed)[/dim]")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Clone/update recently updated repositories")
    parser.add_argument("--limit", type=int, default=3, help="Number of repositories to process (default: 3)")
    parser.add_argument("--days", type=int, default=30, help="Number of days to look back (default: 30)")
    parser.add_argument("--work-dir", type=Path, default=Path("work"), help="Work directory for repos (default: work/)")
    parser.add_argument("--merge-passing", action="store_true", help="Auto-merge PRs that have all checks passed")
    
    args = parser.parse_args()
    
    owner = "dioderobot"
    
    try:
        # Check if source workflow exists
        source_workflow = Path.cwd() / '.github' / 'workflows' / 'pcb-release.yml'
        if not source_workflow.exists():
            console.print(f"[red]Source workflow file not found: {source_workflow}[/red]")
            sys.exit(1)
        
        repos = get_repos_updated_since_days(owner, args.days, args.limit)
        
        display_repos(repos, args.days)
        
        # Setup repositories and update workflows
        setup_repos(repos, args.work_dir, source_workflow, args.merge_passing)
        
    except KeyboardInterrupt:
        console.print("\n[red]Cancelled by user[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
