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
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(__file__)

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
    current_y = "disconnected"
    discovered_patterns = []
    scanned_lines = []
    last_log_timestamp = None

    with open(log_path, 'r') as file:
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
                if current_y is not None:
                    event_details = [timestamp, pattern['status'], f"Line {line_number}: {line.strip()}", current_y, current_y, pattern['name']]
                    discovered_patterns.append(event_details)
                    events.append(
                        {"timestamp": timestamp, "status": pattern["status"], "pattern": f"Line {line_number}: {line.strip()}", "mac": current_y, "y": current_y, "name": pattern["name"]})

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

def create_timeline(events, mac_addresses, mac_info, last_log_timestamp):
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
                text=[connectivity_rssi_texts[i], ""],
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

    fig.write_html('wifi_connectivity_timeline.html', auto_open=True, include_plotlyjs='cdn', full_html=False, config={'scrollZoom': True})

    return fig


def main():
    debug_mode = '-d' in sys.argv
    #lines_mode = '-l' in sys.argv
    lines_mode = 1

    while True:
        if len(sys.argv) > 1:
            log_path = sys.argv[1]
        else:
            log_path = input("Enter the log file path: ")

        with open(log_path, 'r') as file:
            lines = file.readlines()

        if lines_mode:
            start_line_input = input("Enter the start line number (leave empty for first line): ")
            end_line_input = input("Enter the end line number (leave empty for last line): ")
        else:
            start_line_input = 0
            end_line_input = len(lines)

        start_line = int(start_line_input) if start_line_input else 0
        end_line = int(end_line_input) if end_line_input else len(lines)

        events, mac_addresses, mac_info, discovered_patterns, scanned_lines, last_log_timestamp = parse_log(log_path,start_line,end_line)
        fig = create_timeline(events, mac_addresses, mac_info, last_log_timestamp)

        pyo.plot(fig, filename='wifi_connectivity_timeline.html', auto_open=True)

        if debug_mode:
            with pd.ExcelWriter('patterns_discovered.xlsx', engine='openpyxl') as writer:
                patterns_df = pd.DataFrame(discovered_patterns, columns=['Timestamp', 'Status', 'Pattern', 'MAC', 'Y', 'Name'])
                patterns_df.to_excel(writer, sheet_name='Patterns Discovered', index=False)


        choice = input("Do you want to run the program again? (y/n): ").strip().lower()
        if choice != 'y':
            break

if __name__ == "__main__":
    main()