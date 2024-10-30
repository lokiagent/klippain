import re
import argparse
from collections import defaultdict
import os

def parse_config_sections(config_content):
    config_sections = defaultdict(list)

    # Patterns for headers and include lines
    main_header_pattern = re.compile(r'#\s{3}([A-Z\s]+)\s{3}#')  # Detects main headers
    sub_header_pattern = re.compile(r'#\s*-{3,}\s*([A-Z\s]+)\s*-{3,}')  # Detects sub-headers
    include_line_pattern = re.compile(r'#?\s*\[include\s+(.*?)\]')

    current_section = None
    main_section = None

    for line in config_content.splitlines():
        # Check for main header first
        main_header_match = main_header_pattern.match(line)
        if main_header_match:
            main_section = main_header_match.group(1).strip().replace(" ", "_").lower()
            continue

        # Check for sub-header within the main section
        sub_header_match = sub_header_pattern.match(line)
        if sub_header_match:
            sub_section = sub_header_match.group(1).strip().replace(" ", "_").lower()
            current_section = f"{main_section}_{sub_section}" if main_section else sub_section
            continue

        # Capture include lines under the current section
        if current_section:
            include_match = include_line_pattern.match(line)
            if include_match:
                config_file_path = include_match.group(1).strip()
                config_sections[current_section].append(config_file_path)

    return dict(config_sections)

def main():
    parser = argparse.ArgumentParser(description="Parse config sections and includes.")
    parser.add_argument("--config", required=True, help="Path to the config file")
    args = parser.parse_args()

    # Load the config file content
    with open(args.config, "r") as file:
        config_content = file.read()

    # Parse sections
    parsed_sections = parse_config_sections(config_content)

    # Determine output filename
    output_filename = os.path.splitext(os.path.basename(args.config))[0] + '.vars'

    # Write the output to a file
    with open(output_filename, 'w') as output_file:
        for section, files in parsed_sections.items():
            output_file.write(f"{section}: {', '.join(files)}\n")

if __name__ == "__main__":
    main()
