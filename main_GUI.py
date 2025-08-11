import re
import json
import os
import sys
from datetime import datetime
import plotly.graph_objects as go
import plotly.offline as pyo
import subprocess
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QLineEdit, QLabel
from PyQt5.QtGui import QIcon
import chardet

# Load patterns from JSON file
def load_patterns():
    exe_dir = os.path.join(os.path.dirname(__file__), 'patterns.json')
    with open(os.path.join(exe_dir), 'r') as file:
        return json.load(file)

patterns = load_patterns()
connectivity_patterns = patterns['connectivity_patterns']
info_patterns = patterns['info_patterns']
mac_patterns = [re.compile(p) for p in patterns['mac_patterns']]

beacon_rx_pattern = re.compile(
    r'BEACON_RX - (?P<mac>[0-9A-F:]+), channel (?P<channel>\d+)\s*, band (?P<band>[\d._]+GHz), RSSI (?P<rssi>-?\d+), seq \d+\s+"(?P<ssid>[^"]+)"'
)

def parse_log(log_path, start_line, end_line):
    events = []
    mac_info = {}
    mac_addresses = []
    current_y = "disconnected"
    discovered_patterns = []
    scanned_lines = []
    last_log_timestamp = None
    seen_ap_PD_timestamps = set()


    with open(log_path, 'rb') as file:
       raw_data = file.read()
       result = chardet.detect(raw_data)
       encoding = result['encoding']
       #print(f"Detected encoding: {encoding}")

    with open(log_path, 'r', encoding=encoding) as file:
        lines = file.readlines()[start_line:end_line]
        

    for line_number, line in enumerate(lines, start=start_line):
        scanned_lines.append((line_number, line.strip()))

        timestamp_match = re.search(r"(\d{2}/\d{2}/\d{2,4}-\d{2}:\d{2}:\d{2}\.\d{3})", line)
        if timestamp_match:
            last_log_timestamp = datetime.strptime(timestamp_match.group(1), "%m/%d/%Y-%H:%M:%S.%f")

        for pattern in mac_patterns:
            match = pattern.search(line)
            if match:
                mac = match.group(1)
                mac_event_details = [last_log_timestamp, "MAC Address Detected", f"Line {line_number}: {line.strip()}", mac,
                                     current_y, "MAC Address"]
                discovered_patterns.append(mac_event_details)
                mac_addresses = [(ts, m) for ts, m in mac_addresses if m != mac]
                mac_addresses.append((last_log_timestamp, mac))

                current_y = mac

        match = beacon_rx_pattern.search(line)
        if match:
            mac = match.group("mac")
            mac_info[mac] = {
                "ssid": match.group("ssid"),
                "band": match.group("band"),
                "channel": match.group("channel")
            }

        for pattern in connectivity_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                mac = current_y

                rssi_value = None
                if pattern["status"] == "Attempt_to_connect":
                    rssi_match = re.search(r"Rssi:(-?\d+)", line)
                    if rssi_match:
                        rssi_value = rssi_match.group(1)

                if not any(e["timestamp"] == timestamp and e["pattern"].startswith(f"Line {line_number}:") for e in events):
                    current_y = "disconnected" if mac is None or pattern["status"] == "disconnected" or pattern["status"] == "connection_failed" else mac
                    event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", mac, current_y, rssi_value]
                    discovered_patterns.append(event_details)
                    events.append(
                        {"timestamp": timestamp, "status": pattern["status"], "pattern": f"Line {line_number}: {line.strip()}", "mac": mac, "y": current_y, "rssi": rssi_value})

        for pattern in info_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                event = pattern['status']

                if current_y is not None:
                    if pattern["name"] != "AP poorly disc":
                        event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", current_y,
                                         current_y, pattern['name']]
                        discovered_patterns.append(event_details)
                        events.append(
                            {"timestamp": timestamp, "status": pattern["status"],
                             "pattern": f"Line {line_number}: {line.strip()}", "mac": current_y, "y": current_y,
                             "name": pattern["name"]})
                    else:
                        # Check if the event is "AP poorly disc" and if the timestamp has been seen
                        if pattern["name"] == "AP poorly disc":
                            if timestamp not in seen_ap_PD_timestamps:
                                # Check for "PoorlyDisc:25" in the line
                                event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}",
                                                 current_y, current_y, pattern['name'], ]
                                discovered_patterns.append(event_details)
                                events.append(
                                    {"timestamp": timestamp, "status": pattern["status"],
                                     "pattern": f"Line {line_number}: {line.strip()}", "mac": current_y, "y": current_y,
                                     "name": pattern["name"]})
                                seen_ap_PD_timestamps.add(timestamp)
                            else:
                                continue
    # Add the "end" point to the events list
    if last_log_timestamp and events:
        last_event_y = events[-1]["y"]
        events.append({
            "timestamp": last_log_timestamp,
            "status": "end",
            "pattern": "End of Log",
            "mac": None,
            "y": last_event_y,
            "rssi": None
        })

    return events, mac_addresses, mac_info, discovered_patterns, scanned_lines, last_log_timestamp


def check_flow_validity(events):
    """
    Check the validity of the flow according to specified flow rules.
    If an invalid flow is detected, return True. Otherwise, return False.
    """
    invalid_flow_detected = False
    last_attempt_to_connect_timestamp = None

    for event in events:
        status = event["status"]
        y = event["y"]

        # Rule 1: If a connected pattern appears in the "disconnected" mac level.
        if status == "connected" and y == "disconnected":
            invalid_flow_detected = True
            break

        # Rule 2: If "auth_req" pattern is not following "Attempt_to_connect" pattern.
        if status == "Attempt_to_connect":
            last_attempt_to_connect_timestamp = event["timestamp"]
        elif status == "auth_req":
            if last_attempt_to_connect_timestamp is None or event["timestamp"] <= last_attempt_to_connect_timestamp:
                invalid_flow_detected = True
                break
    return invalid_flow_detected


def create_timeline(events, mac_addresses, mac_info, last_log_timestamp, output_filename):
    invalid_flow_detected = check_flow_validity(events)
    y_labels = ["disconnected"] + [mac for _, mac in mac_addresses]
    y_positions = {label: i for i, label in enumerate(y_labels)}

    connectivity_x_values = []
    connectivity_y_values = []
    connectivity_colors = []
    connectivity_hover_texts = []
    connectivity_symbols = []
    connectivity_line_styles = []
    connectivity_rssi_texts = []

    info_x_values = [[] for _ in info_patterns]
    info_y_values = [[] for _ in info_patterns]
    info_hover_texts = [[] for _ in info_patterns]

    info_symbols = [str(i) for i in range(len(info_patterns))]

    suspend_resume_pairs = []

    vertical_line_timestamps = []

    for event in events:
        timestamp = event["timestamp"]
        status = event["status"]
        pattern = event["pattern"]
        y = y_positions[event["y"]]
        rssi_text = event.get("rssi", None) if status == "Attempt_to_connect" else ""

        if status == "info":
            for i, info_pattern in enumerate(info_patterns):
                if re.search(info_pattern["pattern"], pattern):
                    info_x_values[i].append(timestamp)
                    info_y_values[i].append(y)
                    info_hover_texts[i].append(pattern)
            continue

        if status == "Driver disable":
            connectivity_symbols.append('diamond')
            vertical_line_timestamps.append(timestamp)
        elif status == "uCode alive":
            connectivity_symbols.append('diamond')
            vertical_line_timestamps.append(timestamp)

        connectivity_x_values.append(timestamp)
        connectivity_y_values.append(y)
        connectivity_hover_texts.append(pattern)
        connectivity_rssi_texts.append(f"RSSI: {rssi_text}" if rssi_text else "")

        connectivity_symbols.append('circle')
        if status == "disconnected" or status == "connection_failed":
            connectivity_colors.append('red')
            connectivity_line_styles.append('solid')
        elif status == "Deauth by Driver" or status == "connect_failure":
            connectivity_colors.append('red')
            connectivity_line_styles.append('solid')
        elif status == "Deauth from Peer":
            connectivity_colors.append('darkred')
            connectivity_line_styles.append('solid')
        elif status == "auth_req":
            connectivity_colors.append('orange')
            connectivity_line_styles.append('solid')
        elif status == "associated":
            connectivity_colors.append('orange')
            connectivity_line_styles.append('solid')
        elif status == "link_switch_start":
            connectivity_colors.append('magenta')
            connectivity_line_styles.append('solid')
        elif status == "Attempt_to_connect":
            connectivity_colors.append('orange')
            connectivity_line_styles.append('solid')
        elif status == "link_switch_end":
            connectivity_colors.append('green')
            connectivity_line_styles.append('solid')
        elif status == "connected":
            connectivity_colors.append('green')
            connectivity_line_styles.append('solid')
        elif status == "Driver disable":
            connectivity_colors.append('red')
            connectivity_line_styles.append('solid')
        elif status == "uCode alive":
            connectivity_colors.append('red')
            connectivity_line_styles.append('solid')
        elif status == "auth_rsp":
            connectivity_colors.append('orange')
            connectivity_line_styles.append('solid')
        elif status == "suspend":
            connectivity_colors.append('purple')
            connectivity_line_styles.append('solid')
            suspend_resume_pairs.append((timestamp, timestamp))
        elif status == "resume":
            connectivity_colors.append('blue')
            connectivity_line_styles.append('solid')
            if suspend_resume_pairs:
                suspend_resume_pairs[-1] = (suspend_resume_pairs[-1][0], timestamp)
        elif status == "end":
            connectivity_colors.append('black')
            connectivity_line_styles.append('solid')

    fig = go.Figure()

    for i in range(len(connectivity_x_values)):
        if i < len(connectivity_x_values) - 1:
            line_style = 'solid'
            for start, end in suspend_resume_pairs:
                if start <= connectivity_x_values[i] < end:
                    line_style = 'dash'
                    break

            fig.add_trace(go.Scatter(
                x=[connectivity_x_values[i], connectivity_x_values[i + 1]],
                y=[connectivity_y_values[i], connectivity_y_values[i + 1]],
                mode='lines+markers+text',
                marker=dict(color=connectivity_colors[i], symbol=connectivity_symbols[i]),
                line=dict(shape='hv', dash=line_style),
                hovertext=connectivity_hover_texts[i],
                hoverinfo="text",
                text=["", connectivity_rssi_texts[i]],
                textposition="top center",
                name='Connectivity Events',
                showlegend=False
            ))
        else:
            fig.add_trace(go.Scatter(
                x=[connectivity_x_values[i]],
                y=[connectivity_y_values[i]],
                mode='markers+text',
                marker=dict(color=connectivity_colors[i], symbol=connectivity_symbols[i]),
                hovertext=connectivity_hover_texts[i],
                hoverinfo="text",
                text=["", connectivity_rssi_texts[i]],
                textposition="top center",
                name='Connectivity Events',
                showlegend=False
            ))

    for i, info_pattern in enumerate(info_patterns):
        fig.add_trace(go.Scatter(
            x=info_x_values[i],
            y=info_y_values[i],
            mode='markers',
            marker=dict(color='black', symbol=info_symbols[i % len(info_symbols)]),
            hovertext=info_hover_texts[i],
            hoverinfo="text",
            name=f'Info Events: {info_pattern["name"]}',
            visible=True,
            showlegend=True
        ))

    for timestamp in vertical_line_timestamps:
        fig.add_shape(type="line",
                      x0=timestamp, x1=timestamp,
                      y0=0, y1=-0.1,
                      line=dict(color="black", width=2))

    # Update the plot title based on flow validity
    if invalid_flow_detected:
        fig.add_annotation(
            text="***Please note,possible log corruption!***",
            xref="paper", yref="paper",
            x=0.5, y=1.1,  # Positioning the text above the main title
            showarrow=False,
            font=dict(size=16,color="red")
        )

    fig.update_layout(
        title="WiFi timeline",
        xaxis_title="Time",
        yaxis_title="Connectivity State",
        yaxis=dict(
            tickvals=list(y_positions.values()),
            ticktext=[
                f"{mac} ({mac_info[mac]['ssid']}, {mac_info[mac]['band']}, {mac_info[mac]['channel']})" if mac in mac_info else mac
                for mac in y_labels]
        ),
        legend_title_text="Click an event to toggle it off/on",
        updatemenus=[
            {
                'type': 'buttons',
                'buttons': [
                    {
                        'label': 'Show All Info Events',
                        'method': 'update',
                        'args': [
                            {'visible': [True] * len(connectivity_x_values) + [True] * len(info_patterns)},
                        ]
                    },
                    {
                        'label': 'Hide All Info Events',
                        'method': 'update',
                        'args': [
                            {'visible': [True] * len(connectivity_x_values) + [False] * len(info_patterns)},
                        ]
                    }
                ],
                'direction': 'down',
                'x': 1.1,
                'y': 1.1,
                'xanchor': 'left',
                'yanchor': 'top'
            }
        ],
        legend=dict(
            x=1.05,
            y=0.95,
            traceorder='normal',
            itemclick='toggle',
            itemdoubleclick='toggle'
        ),
        dragmode='zoom',
    )

    fig.update_layout(
        xaxis=dict(
            rangeselector=dict(
                buttons=list([
                    dict(count=1, label="1m", step="minute", stepmode="backward"),
                    dict(count=5, label="5m", step="minute", stepmode="backward"),
                    dict(count=1, label="1h", step="hour", stepmode="backward"),
                    dict(step="all")
                ])
            ),
            rangeslider=dict(visible=True),
            type="date"
        )
    )

    # Use the output_filename for the HTML file
    fig.write_html(output_filename, auto_open=True, include_plotlyjs='cdn', full_html=False, config={'scrollZoom': True})

    return fig

def open_text_analyser(log_path):
    script_path = os.path.join(os.path.dirname(__file__), 'TextAnalysisTool.NET.exe')

    for file_name in os.listdir(os.path.dirname(__file__)):
        if file_name.endswith('.tat'):
            filter_file = file_name
            filter_path = os.path.join(os.path.dirname(__file__), filter_file)
            break
        else:
            filter_path=""
    subprocess.Popen([script_path, log_path, f'/filters:{filter_path}'])

class LogAnalyzerApp(QWidget):
    def __init__(self, initial_log_path=None):
        super().__init__()
        self.setWindowTitle('Grapholog - WiFi connectivity log analyser')
        self.setGeometry(100, 100, 100, 100)

        icon_path = os.path.join(os.path.dirname(__file__), 'magnifier.png')
        self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout()

        self.path_label = QLabel('To start, enter log path or choose a file:')
        layout.addWidget(self.path_label)

        path_layout = QHBoxLayout()

        self.select_button = QPushButton('Select File/Submit')
        self.select_button.clicked.connect(self.select_log_file)
        path_layout.addWidget(self.select_button)

        self.path_input = QLineEdit()
        path_layout.addWidget(self.path_input)

        layout.addLayout(path_layout)

        line_input_layout = QHBoxLayout()

        self.start_line_label = QLabel('Start Line (Optional)')
        line_input_layout.addWidget(self.start_line_label)

        self.start_line_input = QLineEdit()
        self.start_line_input.setFixedWidth(100)
        line_input_layout.addWidget(self.start_line_input)

        self.end_line_label = QLabel('End Line (Optional)')
        line_input_layout.addWidget(self.end_line_label)

        self.end_line_input = QLineEdit()
        self.end_line_input.setFixedWidth(100)
        line_input_layout.addWidget(self.end_line_input)

        layout.addLayout(line_input_layout)

        self.open_button = QPushButton('Open in Text Analyser')
        self.open_button.clicked.connect(self.open_text_analyser)
        layout.addWidget(self.open_button)

        self.setLayout(layout)

        if initial_log_path:
            self.path_input.setText(initial_log_path)
            self.process_log_file(initial_log_path)

    def select_log_file(self):
        log_path = self.path_input.text().strip()
        if log_path and os.path.exists(log_path):
            self.process_log_file(log_path)
        else:
            options = QFileDialog.Options()
            options |= QFileDialog.ReadOnly
            log_path, _ = QFileDialog.getOpenFileName(self, "Select Log File", "", "All Files (*);;Log Files (*.log)",
                                                      options=options)
            if log_path:
                self.path_input.setText(log_path)
                self.process_log_file(log_path)

    def process_log_file(self, log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as file:
            lines = file.readlines()

        try:
            start_line = int(self.start_line_input.text()) if self.start_line_input.text() else 0
        except ValueError:
            start_line = 0

        try:
            end_line = int(self.end_line_input.text()) if self.end_line_input.text() else len(lines)
        except ValueError:
            end_line = len(lines)

        start_line = max(0, start_line)
        end_line = min(len(lines), end_line)

        events, mac_addresses, mac_info, discovered_patterns, scanned_lines, last_log_timestamp = parse_log(log_path, start_line, end_line)

        # Extract the base name of the input file and append "graph"
        base_name = os.path.splitext(os.path.basename(log_path))[0]
        output_filename = f"{base_name}_graph.html"

        fig = create_timeline(events, mac_addresses, mac_info, last_log_timestamp, output_filename)

        pyo.plot(fig, filename=output_filename, auto_open=True)

        self.log_path = log_path

    def open_text_analyser(self):
        log_path = self.path_input.text()
        if log_path and os.path.exists(log_path):
            open_text_analyser(log_path)

def main():
    initial_log_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = QApplication(sys.argv)
    window = LogAnalyzerApp(initial_log_path)
    window.show()

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()