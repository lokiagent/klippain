import os
import subprocess
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response
from datetime import datetime
import threading

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Configuration paths
USER_CONFIG_PATH = os.path.expanduser('~/printer_data/config')
FRIX_CONFIG_PATH = os.path.expanduser('~/klippain_config')
BACKUP_PATH = os.path.expanduser('~/klippain_config_backups')
FRIX_BRANCH = 'main'
MOONRAKER_API_URL = 'http://localhost:7125/printer/restart'  # Moonraker API endpoint for restarting Klipper

# Get paths from environment variables
PRINTER_VARS_PATH = os.getenv('PRINTER_VARS_PATH', '~/klippain_config/printer.vars')
PRINTER_CFG_PATH = os.getenv('PRINTER_CFG_PATH', '~/klippain_config/user_templates/printer.cfg')

# Status message storage
install_status = []

def append_status(message):
    """Helper to store status messages for AJAX retrieval."""
    install_status.append(message)

def preflight_checks():
    if os.geteuid() == 0:
        append_status("This script must not be run as root!")
        raise PermissionError("This script must not be run as root.")

    result = subprocess.run(
        ["sudo", "systemctl", "is-active", "--quiet", "klipper.service"],
        check=False
    )
    if result.returncode != 0:
        append_status("Klipper service not found. Please install Klipper first!")
        raise EnvironmentError("Klipper service not found. Please install Klipper first.")
    append_status("Klipper service found! Continuing...")

def check_download():
    if not os.path.exists(FRIX_CONFIG_PATH):
        append_status("Downloading Klippain repository...")
        process = subprocess.Popen(
            ["git", "clone", "-b", FRIX_BRANCH, "https://github.com/Frix-x/klippain.git", FRIX_CONFIG_PATH],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
        for line in stdout.splitlines():
            append_status(line.strip())
        for line in stderr.splitlines():
            append_status(line.strip())
        if process.returncode == 0:
            append_status(f"Saving to: {FRIX_CONFIG_PATH}")
        else:
            append_status("Error during download.")
    else:
        append_status("Repository already exists. Skipping download.")

def backup_config():
    backup_dir = os.path.join(BACKUP_PATH, datetime.now().strftime('%Y_%m_%d-%H%M%S'))
    os.makedirs(backup_dir, exist_ok=True)
    if os.path.exists(USER_CONFIG_PATH):
        subprocess.run(["cp", "-r", USER_CONFIG_PATH, backup_dir], check=True)
        append_status(f"Backup complete: {backup_dir}")
    else:
        append_status("No previous config found, skipping backup...")

def list_mcu_files(mcu_path):
    """Helper to list files in a directory to populate MCU options."""
    files = []
    if os.path.exists(mcu_path):
        files = [f for f in os.listdir(mcu_path) if os.path.isfile(os.path.join(mcu_path, f))]
    return files

def run_parse_config():
    subprocess.run(["python3", "parse_config.py", "--config", "/home/pi/klippain/user_templates/printer.cfg"], check=True)

def parse_printer_vars(file_path):
    configurations = {}
    with open(file_path, 'r') as file:
        for line in file:
            if ':' in line:
                key, value = line.split(':', 1)
                configs = [cfg.strip() for cfg in value.split(',') if cfg.strip()]
                configurations[key.strip()] = configs
    return configurations

def update_printer_config(selected_configs):
    printer_cfg_path = '/home/pi/klippain/user_templates/printer.cfg'
    modified_lines = []  # List to store modified lines for confirmation display

    # Read the file
    with open(printer_cfg_path, 'r') as file:
        lines = file.readlines()

    # Process the file lines
    updated_lines = []
    for line in lines:
        # Uncomment any lines based on selected configurations
        if any(config in line for config in selected_configs) and line.strip().startswith('#'):
            # Uncomment the line and track modification
            uncommented_line = line.lstrip('#').strip() + '\n'
            updated_lines.append(uncommented_line)
            modified_lines.append(uncommented_line.strip())  # Store line without extra newline
        else:
            updated_lines.append(line)

    # Write the updated lines back to the file
    with open(printer_cfg_path, 'w') as file:
        file.writelines(updated_lines)

    return modified_lines  # Return modified lines for confirmation

@app.route('/download_status', methods=['GET'])
def download_status():
    global install_status
    return jsonify(status=install_status)

@app.route('/stream')
def stream():
    def event_stream():
        while True:
            if install_status:
                message = install_status.pop(0)
                yield f'data: {message}\n\n'
    return Response(event_stream(), content_type='text/event-stream')

@app.route('/', methods=['GET', 'POST'])
def download():
    global install_status
    install_status = []  # Reset install status for new session
    if request.method == 'POST':
        download_thread = threading.Thread(target=check_download)
        download_thread.start()
        download_thread.join()  # Wait for the download to complete
        return redirect(url_for('install'))

    return render_template('download.html')

@app.route('/install', methods=['GET', 'POST'])
def install():
    global install_status
    install_status = []  # Reset install status for new session
    if request.method == 'POST':
        # Save user responses in session to be used during AJAX installation
        session['install_confirm'] = request.form.get('install_confirm')
        session['mcu_template'] = request.form.get('mcu_template')
        session['toolhead_template'] = request.form.get('toolhead_template')
        session['mmu_template'] = request.form.get('mmu_template')
        return jsonify(success=True)

    return render_template(
        'install.html',
        main_mcu_files=session.get('main_mcu_files', []),
        toolhead_mcu_files=session.get('toolhead_mcu_files', []),
        mmu_mcu_files=session.get('mmu_mcu_files', [])
    )

def install_config():
    os.makedirs(USER_CONFIG_PATH, exist_ok=True)
    for dir_name in ['config', 'macros', 'scripts', 'moonraker']:
        source_dir = os.path.join(FRIX_CONFIG_PATH, dir_name)
        dest_dir = os.path.join(USER_CONFIG_PATH, dir_name)
        if os.path.islink(dest_dir) or os.path.exists(dest_dir):
            os.remove(dest_dir)  # Remove existing link or file
        os.symlink(source_dir, dest_dir)
    append_status("Configuration files have been installed.")

    # Handle MCU configurations
    mcu_config_path = os.path.join(USER_CONFIG_PATH, 'mcu.cfg')
    mcu_definitions_path = os.path.join(FRIX_CONFIG_PATH, 'config/mcu_definitions')
    with open(mcu_config_path, 'a') as mcu_file:
        # Main MCU
        main_mcu_file = session.get('mcu_template')
        if main_mcu_file:
            main_mcu_path = os.path.join(mcu_definitions_path, 'main', main_mcu_file)
            with open(main_mcu_path, 'r') as file:
                mcu_file.write(file.read())
            append_status(f"Main MCU configuration '{main_mcu_file}' added to mcu.cfg.")

        # Toolhead MCU
        toolhead_mcu_file = session.get('toolhead_template')
        if toolhead_mcu_file:
            toolhead_mcu_path = os.path.join(mcu_definitions_path, 'toolhead', toolhead_mcu_file)
            with open(toolhead_mcu_path, 'r') as file:
                mcu_file.write(file.read())
            append_status(f"Toolhead MCU configuration '{toolhead_mcu_file}' added to mcu.cfg.")

        # MMU MCU
        mmu_mcu_file = session.get('mmu_template')
        if mmu_mcu_file:
            mmu_mcu_path = os.path.join(mcu_definitions_path, 'mmu', mmu_mcu_file)
            with open(mmu_mcu_path, 'r') as file:
                mcu_file.write(file.read())
            append_status(f"MMU MCU configuration '{mmu_mcu_file}' added to mcu.cfg.")

def restart_klipper():
    """Restart Klipper using the Moonraker API."""
    try:
        response = requests.post(MOONRAKER_API_URL)
        if response.status_code == 200:
            append_status("Klipper has been successfully restarted through Moonraker.")
        else:
            append_status(f"Failed to restart Klipper via Moonraker: {response.text}")
            raise Exception("Moonraker restart failed")
    except requests.RequestException as e:
        append_status(f"Error communicating with Moonraker API: {e}")
        raise

def run_parse_config():
    subprocess.run(["python3", "parse_config.py", "--config", "/home/pi/klippain/user_templates/printer.cfg"], check=True)

def parse_printer_vars(file_path):
    configurations = {}
    with open(file_path, 'r') as file:
        for line in file:
            if ':' in line:
                key, value = line.split(':', 1)
                configs = [cfg.strip() for cfg in value.split(',') if cfg.strip()]
                configurations[key.strip()] = configs
    return configurations

def update_printer_config(selected_configs):
    printer_cfg_path = '/home/pi/klippain/user_templates/printer.cfg'
    modified_lines = []  # List to store modified lines for confirmation display

    # Read the file
    with open(printer_cfg_path, 'r') as file:
        lines = file.readlines()

    # Process the file lines
    updated_lines = []
    for line in lines:
        # Uncomment any lines based on selected configurations
        if any(config in line for config in selected_configs) and line.strip().startswith('#'):
            # Uncomment the line and track modification
            uncommented_line = line.lstrip('#').strip() + '\n'
            updated_lines.append(uncommented_line)
            modified_lines.append(uncommented_line.strip())  # Store line without extra newline
        else:
            updated_lines.append(line)

    # Write the updated lines back to the file
    with open(printer_cfg_path, 'w') as file:
        file.writelines(updated_lines)

    return modified_lines  # Return modified lines for confirmation

@app.route('/install_progress', methods=['GET'])
def install_progress():
    try:
        # Check if installation was already completed
        if session.get('installation_complete'):
            append_status("Installation already completed.")
            return jsonify(status=install_status, completed=True)  # Send completed flag

        # Only run preflight checks if they haven’t been completed
        if not session.get('preflight_completed'):
            preflight_checks()
            session['preflight_completed'] = True  # Mark as done

        # Only run backup if it hasn’t been completed
        if not session.get('backup_completed'):
            backup_config()
            session['backup_completed'] = True  # Mark as done

        # Only install config if it hasn’t been completed
        if not session.get('config_installed'):
            install_config()
            session['config_installed'] = True  # Mark as done

        # Only restart Klipper if it hasn’t been restarted yet
        if not session.get('klipper_restarted'):
            restart_klipper()
            session['klipper_restarted'] = True  # Mark as done

        # Mark entire installation as complete once all steps are done
        session['installation_complete'] = True
        append_status("Installation completed successfully.")
        return jsonify(status=install_status, completed=True)  # Send completed flag

    except Exception as e:
        append_status(f"Error during installation: {e}")
        return jsonify(status=install_status)
    
@app.route('/configure', methods=['GET', 'POST'])
def configure():
    file_path = PRINTER_VARS_PATH  # Use environment variable
    configurations = parse_printer_vars(file_path)
    if request.method == 'POST':
        selected_configs = request.form.getlist('configs')  # Multi-select
        radio_selections = {k: v for k, v in request.form.items() if k.startswith('configs_') and v}  # Single-select
        # Process selected configurations
        modifications = []
        # Uncomment selected checkbox items
        for config in selected_configs:
            with open(PRINTER_CFG_PATH, 'r') as file:  # Use environment variable
                lines = file.readlines()
            for i, line in enumerate(lines):
                if config in line and line.startswith('#'):
                    lines[i] = line.lstrip('#').rstrip() + '\n'  # Uncomment line
                    modifications.append(line.strip())  # Add the modified line for output
            with open(PRINTER_CFG_PATH, 'w') as file:  # Use environment variable
                file.writelines(lines)
        # Uncomment selected radio items
        for key, selected_radio in radio_selections.items():
            with open(PRINTER_CFG_PATH, 'r') as file:  # Use environment variable
                lines = file.readlines()
            for i, line in enumerate(lines):
                if selected_radio in line and line.startswith('#'):
                    lines[i] = line.lstrip('#').rstrip() + '\n'  # Uncomment line
                    modifications.append(line.strip())  # Add the modified line for output
            with open(PRINTER_CFG_PATH, 'w') as file:  # Use environment variable
                file.writelines(lines)
        return render_template('confirmation.html', modifications=modifications)
    return render_template('configure.html', configure=configure)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
