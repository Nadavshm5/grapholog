import re
import pandas as pd
import json
import os
import sys
from datetime import datetime
import plotly.graph_objects as go
import plotly.offline as pyo

# Load patterns from JSON file
def load_patterns():
    # Determine the directory of the executable
    if getattr(sys, 'frozen', False):
        # If the script is running as a bundled executable
        exe_dir = os.path.dirname(sys.executable)
    else:
        # If the script is running as a normal Python script
        exe_dir = os.path.dirname(__file__)

    # Read patterns.json from the executable's directory
    with open(os.path.join(exe_dir, 'patterns.json'), 'r') as file:
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
    current_y = "disconnected"  # Default value for current_y
    discovered_patterns = []  # List to store discovered patterns
    scanned_lines = []  # List to store scanned lines

    with open(log_path, 'r') as file:
        lines = file.readlines()[start_line:end_line]

    for line_number, line in enumerate(lines, start=start_line):
        scanned_lines.append((line_number, line.strip()))  # Record the line number and content

        # Check for MAC address updates
        for pattern in mac_patterns:
            match = pattern.search(line)
            if match:
                timestamp_match = re.search(r"(\d{2}/\d{2}/\d{2,4}-\d{2}:\d{2}:\d{2}\.\d{3})", line)
                if timestamp_match:
                    timestamp = datetime.strptime(timestamp_match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                    mac = match.group(1)
                    #print the MAC in the debug CSV
                    mac_event_details = [timestamp, "MAC Address Detected", f"Line {line_number}: {line.strip()}", mac,
                                         current_y, "MAC Address"]
                    discovered_patterns.append(mac_event_details)
                    # Remove previous appearance and re-insert at the last position
                    mac_addresses = [(ts, m) for ts, m in mac_addresses if m != mac]
                    mac_addresses.append((timestamp, mac))

                    current_y = mac  # Update current_y with the current MAC
                    # Add MAC address details to discovered patterns
                    #mac_event_details = [timestamp, "MAC Address Detected", f"Line {line_number}: {line.strip()}", mac,
                     #                    current_y, "MAC Address"]
                    #discovered_patterns.append(mac_event_details)

        # Extract MAC info
        match = beacon_rx_pattern.search(line)
        if match:
            mac = match.group("mac")
            mac_info[mac] = {
                "ssid": match.group("ssid"),
                "band": match.group("band"),
                "channel": match.group("channel")
            }

        # Extract connectivity events
        for pattern in connectivity_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                mac = current_y  # Use the current_y which is updated with the latest MAC

                # Allow events with the same timestamp if their line number is different
                if not any(e["timestamp"] == timestamp and e["pattern"].startswith(f"Line {line_number}:") for e in events):
                    current_y = "disconnected" if mac is None or pattern["status"] == "disconnected" or pattern["status"] == "connection_failed" else mac
                    event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", mac, current_y]
                    discovered_patterns.append(event_details)
                    events.append(
                        {"timestamp": timestamp, "status": pattern["status"], "pattern": f"Line {line_number}: {line.strip()}", "mac": mac, "y": current_y})

        # Extract informational events
        for pattern in info_patterns:
            match = re.search(pattern["pattern"], line)
            if match:
                timestamp = datetime.strptime(match.group(1), "%m/%d/%Y-%H:%M:%S.%f")
                if current_y is not None:
                    event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", current_y, current_y, pattern['name']]
                    discovered_patterns.append(event_details)
                    events.append(
                        {"timestamp": timestamp, "status": pattern["status"], "pattern": f"Line {line_number}: {line.strip()}", "mac": current_y, "y": current_y, "name": pattern["name"]})

    return events, mac_addresses, mac_info, discovered_patterns, scanned_lines

def create_timeline(events, mac_addresses, mac_info):
    y_labels = ["disconnected"] + [mac for _, mac in mac_addresses]
    y_positions = {label: i for i, label in enumerate(y_labels)}

    # Separate lists for connectivity and info events
    connectivity_x_values = []
    connectivity_y_values = []
    connectivity_colors = []
    connectivity_hover_texts = []
    connectivity_symbols = []
    connectivity_line_styles = []

    info_x_values = [[] for _ in info_patterns]
    info_y_values = [[] for _ in info_patterns]
    info_hover_texts = [[] for _ in info_patterns]

    # Define different symbols for each info pattern
    info_symbols = [str(i) for i in range(len(info_patterns))]

    suspend_resume_pairs = []  # List to store pairs of suspend and resume events

    for event in events:
        timestamp = event["timestamp"]
        status = event["status"]
        pattern = event["pattern"]
        y = y_positions[event["y"]]

        if status == "info":
            for i, info_pattern in enumerate(info_patterns):
                if re.search(info_pattern["pattern"], pattern):
                    info_x_values[i].append(timestamp)
                    info_y_values[i].append(y)
                    info_hover_texts[i].append(pattern)
            continue

        # Add to connectivity events
        connectivity_x_values.append(timestamp)
        connectivity_y_values.append(y)
        connectivity_hover_texts.append(pattern)

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
        elif status == "auth_rsp":
            connectivity_colors.append('orange')
            connectivity_line_styles.append('solid')
        elif status == "suspend":
            connectivity_colors.append('purple')
            connectivity_line_styles.append('solid')
            suspend_resume_pairs.append((timestamp, None))  # Add suspend event
        elif status == "resume":
            connectivity_colors.append('blue')
            connectivity_line_styles.append('solid')
            if suspend_resume_pairs:
                suspend_resume_pairs[-1] = (suspend_resume_pairs[-1][0], timestamp)  # Add resume event

    # Create the plot
    fig = go.Figure()

    # Add connectivity events trace
    for i in range(len(connectivity_x_values) - 1):
        # Determine if the line should be dashed
        line_style = 'solid'  # Default to solid
        for start, end in suspend_resume_pairs:
            if start <= connectivity_x_values[i] < end:
                line_style = 'dash'  # Set to dash only between suspend and resume
                break

        fig.add_trace(go.Scatter(
            x=[connectivity_x_values[i], connectivity_x_values[i + 1]],
            y=[connectivity_y_values[i], connectivity_y_values[i + 1]],
            mode='lines+markers',
            marker=dict(color=connectivity_colors[i], symbol=connectivity_symbols[i]),
            line=dict(shape='hv', dash=line_style),  # Set line style here
            hovertext=connectivity_hover_texts[i],
            hoverinfo="text",
            name='Connectivity Events',
            showlegend=False  # Set showlegend to False to remove from legend
        ))

    # Add informational events traces
    for i, info_pattern in enumerate(info_patterns):
        fig.add_trace(go.Scatter(
            x=info_x_values[i],
            y=info_y_values[i],
            mode='markers',
            marker=dict(color='black', symbol=info_symbols[i % len(info_symbols)]),
            hovertext=info_hover_texts[i],
            hoverinfo="text",
            name=f'Info Events: {info_pattern["name"]}',
            visible=True,  # Initially visible in the plot
            showlegend=True  # Ensure it appears in the legend
        ))

    # Add buttons for "Hide All" and "Show All"
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
        legend_title_text="Click an event to toggle it off/on",  # Add legend header
        updatemenus=[
            {
                'type': 'buttons',
                'buttons': [
                    {
                        'label': 'Show All Info Events',
                        'method': 'update',
                        'args': [
                            {'visible': [True] * len(connectivity_x_values) + [True] * len(info_patterns)},  # Show all traces
                        ]
                    },
                    {
                        'label': 'Hide All Info Events',
                        'method': 'update',
                        'args': [
                            {'visible': [True] * len(connectivity_x_values) + [False] * len(info_patterns)},  # Hide info events traces
                        ]
                    }
                ],
                'direction': 'down',  # Stack buttons vertically
                'x': 1.1,  # Position the buttons above the legend
                'y': 1.1,  # Adjust the y position to be above the legend
                'xanchor': 'left',
                'yanchor': 'top'
            }
        ],
        legend=dict(
            x=1.05,  # Align legend with buttons
            y=0.95,  # Position legend below buttons
            traceorder='normal',
            itemclick='toggle',  # Toggle individual traces
            itemdoubleclick='toggle'  # Toggle individual traces
        ),
        dragmode='zoom',  # Enable zooming
    )

    # Add custom JavaScript for right-click zoom
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

    # Add custom JavaScript for right-click zoom
    fig.write_html('wifi_connectivity_timeline.html', auto_open=True, include_plotlyjs='cdn', full_html=False, config={'scrollZoom': True})

    return fig

def main():
    #debug_mode = '-d' in sys.argv  # Check for debug flag
    debug_mode=1
    lines_mode = '-l' in sys.argv  # Check for lines flag



    while True:
        # Determine log file path based on command-line arguments or user input
        if len(sys.argv) > 1:
            log_path = sys.argv[1]
        else:
            log_path = input("Enter the log file path: ")

        # Read the entire file to determine the number of lines
        with open(log_path, 'r') as file:
            lines = file.readlines()

        # Prompt user for start line and end line
        if lines_mode:
            start_line_input = input("Enter the start line number (leave empty for first line): ")
            end_line_input = input("Enter the end line number (leave empty for last line): ")
        else:
            start_line_input = 0
            end_line_input = len(lines)


        # Set default values if input is empty
        start_line = int(start_line_input) if start_line_input else 0
        end_line = int(end_line_input) if end_line_input else len(lines)

        events, mac_addresses, mac_info, discovered_patterns, scanned_lines = parse_log(log_path, start_line, end_line)

        fig = create_timeline(events, mac_addresses, mac_info)

        pyo.plot(fig, filename='wifi_connectivity_timeline.html', auto_open=True)

        # Write to Excel file with multiple sheets if debug mode is enabled
        if debug_mode:
            with pd.ExcelWriter('patterns_discovered.xlsx', engine='openpyxl') as writer:
                patterns_df = pd.DataFrame(discovered_patterns, columns=['Timestamp', 'Status', 'Pattern', 'MAC', 'Y', 'Name'])
                patterns_df.to_excel(writer, sheet_name='Patterns Discovered', index=False)

        # Ask the user if they want to run the program again
        choice = input("Do you want to run the program again? (y/n): ").strip().lower()
        if choice != 'y':
            break

# Run the main function
if __name__ == "__main__":
    main()