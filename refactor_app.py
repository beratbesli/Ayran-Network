import re

with open('beer_network/app.py', 'r') as f:
    content = f.read()

# 1. Remove AI imports and constants
content = re.sub(r'from beer_network\.ai_analysis import [^\n]+\n', '', content)
content = re.sub(r'_AI_[^\n]+\n(?:    [^\n]+\n)*', '', content)

# 2. Change columns
content = content.replace('("Estimated Speed", "estimated-speed", 22)', '("Activity Score", "activity-score", 14)')
content = content.replace('("Traffic", "estimated-speed", 17)', '("Activity", "activity-score", 10)')

# 3. Remove AIAnalyzer protocol
content = re.sub(r'class AIAnalyzer\(Protocol\):.*?(?=class ProcessActionConfirmScreen)', '', content, flags=re.DOTALL)

# 4. Remove AIAnalysisScreen class
content = re.sub(r'class AIAnalysisScreen\(ModalScreen\[None\]\):.*?(?=class ProcessDetailsScreen)', '', content, flags=re.DOTALL)

# 5. Fix ProcessDetailsScreen
content = content.replace(
    '        upload_history: tuple[float, ...],\n        download_history: tuple[float, ...],',
    '        activity_history: tuple[float, ...],'
)
content = content.replace('self.upload_history = upload_history\n        self.download_history = download_history', 'self.activity_history = activity_history')

content = re.sub(
    r'f"Est\. Upload: \{format_rate\(self\.process\.estimated_upload_bytes_per_second\)\} \| "\n\s*f"Est\. Download: \{format_rate\(self\.process\.estimated_download_bytes_per_second\)\}"',
    r'f"Activity Score: {self.process.activity_score:.1f}"',
    content
)

content = re.sub(
    r'yield Static\("Upload History", classes="metric-name"\)\n\s*yield Sparkline\(\n\s*self\.upload_history,\n\s*min_color="#4b8bd8",\n\s*max_color="#5eead4",\n\s*classes="metric-sparkline",\n\s*\)\n\s*yield Static\("Download History", classes="metric-name"\)\n\s*yield Sparkline\(\n\s*self\.download_history,\n\s*min_color="#4b8bd8",\n\s*max_color="#f9a8d4",\n\s*classes="metric-sparkline",\n\s*\)',
    r'yield Static("Activity History", classes="metric-name")\n            yield Sparkline(\n                self.activity_history,\n                min_color="#4b8bd8",\n                max_color="#5eead4",\n                classes="metric-sparkline",\n            )',
    content
)

# 6. Remove AI Binding and parameters
content = re.sub(r'\s*Binding\("a", "analyze_process", "AI Analyze"\),\n', '\n', content)
content = re.sub(r'\s*analyzer: AIAnalyzer \| None = None,\n', '\n', content)
content = re.sub(r'\s*self\.analyzer: AIAnalyzer = \(\n\s*analyzer if analyzer is not None else AIAnalysisService\.from_environment\(\)\n\s*\)\n', '', content)
content = re.sub(r'\s*self\._owns_analyzer = analyzer is None\n', '', content)
content = re.sub(r'\s*self\._analysis_running = False\n', '', content)

content = re.sub(r'\s*analysis_workers = self\.workers\.cancel_group\(self, "ai-analysis"\)\n\s*if analysis_workers:\n\s*await asyncio\.gather\(\n\s*\*\(\w+\.wait\(\) for \w+ in analysis_workers\),\n\s*return_exceptions=True,\n\s*\)\n', '', content)

content = re.sub(r'\s*if self\._owns_analyzer:\n\s*await self\.analyzer\.aclose\(\)\n', '', content)

# 7. Change _process_upload_history and _process_download_history
content = content.replace('self._process_upload_history: dict[int, deque[float]] = {}', 'self._process_activity_history: dict[int, deque[float]] = {}')
content = content.replace('self._process_download_history: dict[int, deque[float]] = {}', '')

# 8. Update _process_cells
cell_replacement = '''
        is_high_traffic = process.activity_score >= 10.0

        display_name = f"🚨 {process.name}" if is_high_traffic else process.name
        name = Text(
            display_name,
            overflow="ellipsis",
            no_wrap=True,
            style="bold red" if is_high_traffic else "bold white",
        )

        raw_status = _display_status(process)
        status = Text(raw_status, overflow="ellipsis", no_wrap=True)
        if "running" in raw_status:
            status.stylize("bold green")
        elif "sleeping" in raw_status:
            status.stylize("cyan")
        elif "suspended" in raw_status or "stopped" in raw_status:
            status.stylize("bold yellow")
        elif "zombie" in raw_status or "dead" in raw_status:
            status.stylize("bold red")
        else:
            status.stylize("dim white")

        score = Text(f"{process.activity_score:.1f}", overflow="ellipsis", no_wrap=True)
        if process.activity_score >= 10.0:
            score.stylize("bold red")
        elif process.activity_score > 0:
            score.stylize("bold green")
        else:
            score.stylize("dim")

        remote_text = self._format_remote_endpoint(process)
        remote = Text(remote_text, overflow="ellipsis", no_wrap=True)
        if "—" not in remote_text:
            remote.stylize("bright_blue")
        else:
            remote.stylize("dim")

        if compact:
            return (
                str(process.pid),
                name,
                status,
                str(process.connection_count),
                score,
                remote,
            )
        else:
            return (
                str(process.pid),
                name,
                process.username,
                status,
                str(process.connection_count),
                score,
                remote,
            )
'''

content = re.sub(r'        is_high_traffic = \(\n.*?return \(\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\)[\n ]*else:.*?return \(\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\n.*?\)', cell_replacement, content, flags=re.DOTALL)

# Update action_show_details
details_action = '''        activity_history = self._process_activity_history.get(process.pid)
        self.push_screen(
            ProcessDetailsScreen(
                process,
                tuple(activity_history) if activity_history else (0.0, 0.0),
            )
        )'''
content = re.sub(r'        upload_history = self\._process_upload_history.*?tuple\(download_history\) if download_history else \(0\.0, 0\.0\),\n\s*\)\n\s*\)', details_action, content, flags=re.DOTALL)

# Update _update_process_histories
hist_update = '''    def _update_process_histories(self, processes: Sequence[ProcessSnapshot]) -> None:
        active_pids = {process.pid for process in processes}
        for pid in list(self._process_activity_history.keys()):
            if pid not in active_pids:
                del self._process_activity_history[pid]

        for process in processes:
            history = self._process_activity_history.setdefault(
                process.pid,
                deque([0.0, 0.0], maxlen=self._history_size),
            )
            history.append(process.activity_score)'''
content = re.sub(r'    def _update_process_histories.*?history\.append\(process\.estimated_download_bytes_per_second\)', hist_update, content, flags=re.DOTALL)


# Remove AI actions at the bottom
content = re.sub(r'    async def action_analyze_process.*?self\.push_screen\(AIAnalysisScreen\(process, result\)\)', '', content, flags=re.DOTALL)

# Add sorting to _render_processes
content = re.sub(
    r'        focus_selection = self\.classifier\.split\(processes\)',
    r'        processes = sorted(processes, key=lambda p: p.activity_score, reverse=True)\n        focus_selection = self.classifier.split(processes)',
    content
)

# Warn if no root
content = re.sub(
    r'        self\.query_one\("#status", Static\)\.update\(".*?"\)',
    r'        status_msg = f"Last sample: {snapshot.processes[0].connection_count if snapshot.processes else 0} processes"\n        if getattr(os, "geteuid", lambda: -1)() != 0:\n            status_msg = "⚠️ WARNING: Running without sudo/root. Full network activity is hidden! "\n            self.query_one("#status", Static).set_classes("error")\n        self.query_one("#status", Static).update(status_msg)',
    content
)

content = content.replace('"AIAnalysisScreen",', '')
content = content.replace('"AIAnalyzer",', '')

with open('beer_network/app.py', 'w') as f:
    f.write(content)

