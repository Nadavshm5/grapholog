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

# Load patterns from JSON file
def load_patterns():
    # Define the path to the JSON file containing patterns
    exe_dir = os.path.join(os.path.dirname(__file__), 'patterns.json')
    # Open the JSON file and load its contents
    with open(os.path.join(exe_dir), 'r') as file:
        return json.load(file)

# Load patterns from the JSON file
patterns = load_patterns()
connectivity_patterns = patterns['connectivity_patterns']
info_patterns = patterns['info_patterns']
mac_patterns = [re.compile(p) for p in patterns['mac_patterns']]

# Define a regex pattern for parsing beacon reception events
beacon_rx_pattern = re.compile(
    r'BEACON_RX - (?P<mac>[0-9A-F:]+), channel (?P<channel>\d+)\s*, band (?P<band>[\d._]+GHz), RSSI (?P<rssi>-?\d+), seq \d+\s+"(?P<ssid>[^"]+)"'
)

# Function to parse the log file
# Function to parse the log file
def parse_log(log_path, start_line, end_line):
    events = []  # List to store parsed events
    mac_info = {}  # Dictionary to store MAC information
    mac_addresses = []  # List to store detected MAC addresses
    current_y = "disconnected"  # Current connectivity state
    discovered_patterns = []  # List to store discovered patterns
    scanned_lines = []  # List to store scanned lines

    # Open the log file and read lines within the specified range
    with open(log_path, 'r') as file:
        lines = file.readlines()[start_line:end_line]

    # Iterate over each line in the log file
    for line_number, line in enumerate(lines, start=start_line):
        scanned_lines.append((line_number, line.strip()))

        # Check for MAC address patterns in the line
        for pattern in mac_patterns:
            match = pattern.search(line)
            if match:
                # Extract timestamp from the line
                timestamp_match = re.search(r"(\d{2}/\d{2}/\d{2,4}-\d{2}:\d{2}:\d{2}\.\d{3})", line)
                if timestamp_match:
                    timestamp = datetime.strptime(timestamp_match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                    mac = match.group(1)
                    mac_event_details = [timestamp, "MAC Address Detected", f"Line {line_number}: {line.strip()}", mac,
                                         current_y, "MAC Address"]
                    discovered_patterns.append(mac_event_details)
                    mac_addresses = [(ts, m) for ts, m in mac_addresses if m != mac]
                    mac_addresses.append((timestamp, mac))

                    current_y = mac

        # Check for beacon reception pattern in the line
        match = beacon_rx_pattern.search(line)
        if match:
            mac = match.group("mac")
            mac_info[mac] = {
                "ssid": match.group("ssid"),
                "band": match.group("band"),
                "channel": match.group("channel")
            }

        # Check for connectivity patterns in the line
        for pattern in connectivity_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                mac = current_y

                # Extract RSSI value for "Attempt to connect" events
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

        # Check for info patterns in the line
        for pattern in info_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                if current_y is not None:
                    event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", current_y, current_y, pattern['name']]
                    discovered_patterns.append(event_details)
                    events.append(
                        {"timestamp": timestamp, "status": pattern["status"], "pattern": f"Line {line_number}: {line.strip()}", "mac": current_y, "y": current_y, "name": pattern["name"]})
    #print(f"discovered_patterns:{discovered_patterns}")
    #print(f"scanned lines:{scanned_lines}")
    #print(f"events:{events}")
    return events, mac_addresses, mac_info, discovered_patterns, scanned_lines

# Function to create a timeline visualization using Plotly
def create_timeline(events, mac_addresses, mac_info):
    # Define y-axis labels and positions
    y_labels = ["disconnected"] + [mac for _, mac in mac_addresses]
    y_positions = {label: i for i, label in enumerate(y_labels)}

    # Initialize lists to store connectivity and info event data
    connectivity_x_values = []
    connectivity_y_values = []
    connectivity_colors = []
    connectivity_hover_texts = []
    connectivity_symbols = []
    connectivity_line_styles = []
    connectivity_rssi_texts = []  # New list to store RSSI text

    info_x_values = [[] for _ in info_patterns]
    info_y_values = [[] for _ in info_patterns]
    info_hover_texts = [[] for _ in info_patterns]

    info_symbols = [str(i) for i in range(len(info_patterns))]

    suspend_resume_pairs = []

    # Track timestamps for vertical lines
    vertical_line_timestamps = []

    # Iterate over each event to populate data for visualization
    for event in events:
        timestamp = event["timestamp"]
        status = event["status"]
        pattern = event["pattern"]
        y = y_positions[event["y"]]
        rssi_text = event.get("rssi", None) if status == "Attempt_to_connect" else ""  # Only add RSSI text for "Attempt_to_connect"


        # Handle info events separately
        if status == "info":
            for i, info_pattern in enumerate(info_patterns):
                if re.search(info_pattern["pattern"], pattern):
                    info_x_values[i].append(timestamp)
                    info_y_values[i].append(y)
                    info_hover_texts[i].append(pattern)
            continue

        # Check for "Driver disable" events to add vertical lines
        if status == "Driver disable":
            connectivity_symbols.append('diamond')
            vertical_line_timestamps.append(timestamp)
        elif status == "uCode alive":
            connectivity_symbols.append('diamond')
            vertical_line_timestamps.append(timestamp)

        # Append connectivity event data
        connectivity_x_values.append(timestamp)
        connectivity_y_values.append(y)
        connectivity_hover_texts.append(pattern)
        connectivity_rssi_texts.append(f"RSSI: {rssi_text}" if rssi_text else "")  # Add RSSI text

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

    # Create a Plotly figure for visualization
    fig = go.Figure()


    # Add connectivity event traces to the figure
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
                text=[connectivity_rssi_texts[i],""],  # Add RSSI text
                textposition="top center",  # Position RSSI text above the point
                name='Connectivity Events',
                showlegend=False
            ))
        else:
            # Handle the last point separately if needed
            fig.add_trace(go.Scatter(
                x=[connectivity_x_values[i]],
                y=[connectivity_y_values[i]],
                mode='markers+text',
                marker=dict(color=connectivity_colors[i], symbol=connectivity_symbols[i]),
                hovertext=connectivity_hover_texts[i],
                hoverinfo="text",
                text=[connectivity_rssi_texts[i],""],  # Add RSSI text
                textposition="top center",  # Position RSSI text above the point
                name='Connectivity Events',
                showlegend=False
            ))

    # Add info event traces to the figure
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

    # Add vertical lines for "Driver disable" events
    for timestamp in vertical_line_timestamps:
        fig.add_shape(type="line",
                      x0=timestamp, x1=timestamp,
                      y0=0, y1=-0.1,  # Extend the line below the "disconnected" level
                      line=dict(color="black", width=2))

    # Update layout settings for the figure
    fig.update_layout(
        title="WiFi Connectivity Timeline",
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

    # Add range selector and slider to the x-axis
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

    # Save the figure as an HTML file and open it in the browser
    fig.write_html('wifi_connectivity_timeline.html', auto_open=True, include_plotlyjs='cdn', full_html=False, config={'scrollZoom': True})

    return fig

# Function to open the log file in a text analysis tool
def open_text_analyser(log_path):
    # Define the path to the text analysis tool executable
    script_path = os.path.join(os.path.dirname(__file__), 'TextAnalysisTool.NET.exe')

    # Iterate over the files in the directory
    for file_name in os.listdir(os.path.dirname(__file__)):
        # Check if the file name ends with '.tat'
        if file_name.endswith('.tat'):
            filter_file = file_name
            filter_path = os.path.join(os.path.dirname(__file__), filter_file)
            break  # Stop after finding the first file
        else:
            filter_path=""
    # Run the text analysis tool with the log file and filter
    subprocess.Popen([script_path, log_path, f'/filters:{filter_path}'])




# Define the main application class for the log analyzer
class LogAnalyzerApp(QWidget):
    def __init__(self, initial_log_path=None):
        super().__init__()
        self.setWindowTitle('Grapholog - WiFi connectivity log analyser')
        self.setGeometry(100, 100, 100, 100)  # Adjusted height to accommodate new input fields

        # Set the window icon
        icon_path = os.path.join(os.path.dirname(__file__), 'magnifier.png')
        self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout()

        self.path_label = QLabel('To start, enter log path or choose a file:')
        layout.addWidget(self.path_label)

        # Create a horizontal layout for the select button and path input
        path_layout = QHBoxLayout()

        self.select_button = QPushButton('Select File/Submit')
        self.select_button.clicked.connect(self.select_log_file)
        path_layout.addWidget(self.select_button)

        self.path_input = QLineEdit()
        path_layout.addWidget(self.path_input)

        layout.addLayout(path_layout)

        # Create horizontal layouts for start and end line inputs below the path selection
        line_input_layout = QHBoxLayout()

        self.start_line_label = QLabel('Start Line (Optional)')
        line_input_layout.addWidget(self.start_line_label)

        self.start_line_input = QLineEdit()
        self.start_line_input.setFixedWidth(100)  # Set fixed width for start line input
        line_input_layout.addWidget(self.start_line_input)

        self.end_line_label = QLabel('End Line (Optional)')
        line_input_layout.addWidget(self.end_line_label)

        self.end_line_input = QLineEdit()
        self.end_line_input.setFixedWidth(100)  # Set fixed width for end line input
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
            # Change the default filter to "All Files (*)"
            log_path, _ = QFileDialog.getOpenFileName(self, "Select Log File", "", "All Files (*);;Log Files (*.log)",
                                                      options=options)
            if log_path:
                self.path_input.setText(log_path)
                self.process_log_file(log_path)

    def process_log_file(self, log_path):
        with open(log_path, 'r') as file:
            lines = file.readlines()

        # Determine start and end lines based on user input
        try:
            start_line = int(self.start_line_input.text()) if self.start_line_input.text() else 0
        except ValueError:
            start_line = 0

        try:
            end_line = int(self.end_line_input.text()) if self.end_line_input.text() else len(lines)
        except ValueError:
            end_line = len(lines)

        # Ensure start_line and end_line are within valid range
        start_line = max(0, start_line)
        end_line = min(len(lines), end_line)

        events, mac_addresses, mac_info, discovered_patterns, scanned_lines = parse_log(log_path, start_line, end_line)

        fig = create_timeline(events, mac_addresses, mac_info)

        pyo.plot(fig, filename='wifi_connectivity_timeline.html', auto_open=True)

        self.log_path = log_path

    def open_text_analyser(self):
        log_path = self.path_input.text()
        if log_path and os.path.exists(log_path):
            open_text_analyser(log_path)

# Main function to run the application
def main():
    initial_log_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = QApplication(sys.argv)
    window = LogAnalyzerApp(initial_log_path)
    window.show()

    sys.exit(app.exec_())

# Entry point of the script
if __name__ == "__main__":
    main()