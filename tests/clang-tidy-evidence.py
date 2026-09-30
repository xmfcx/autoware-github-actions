import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def capture(phase):
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    source = (workspace / os.environ['SOURCE_DIRECTORY']).resolve()
    case = os.environ['EVIDENCE_CASE']
    target = workspace / 'evidence' / case / phase
    target.mkdir(parents=True, exist_ok=True)
    report_path = Path('/tmp/clang-tidy-result/report.log')
    assert report_path.is_file(), 'Action did not produce report.log'
    report = report_path.read_text()
    shutil.copytree('/tmp/clang-tidy-result', target / 'report', dirs_exist_ok=True)
    database_path = workspace / 'build/compile_commands.json'
    assert database_path.is_file(), 'No compilation database at workspace root'
    shutil.copy(database_path, target / 'compile_commands.json')
    shutil.copy(source / '.clang-tidy', target / 'source-clang-tidy.yaml')
    if (workspace / '.clang-tidy').is_file():
        shutil.copy(workspace / '.clang-tidy', target / 'workspace-clang-tidy.yaml')
    selected = sorted({str(Path(file).resolve().relative_to(source))
                       for file in re.findall(r'(/[^\s]*checks/[^\s:]+\.cpp)', report)})
    expected_config = Path(os.environ['RUNNER_TEMP']) / 'source-directory-clang-tidy.yaml'
    supported = case not in ['before-nested', 'after-nested-default']
    result = {'case': case, 'distro': os.environ['EVIDENCE_DISTRO'], 'phase': phase,
              'revision': os.environ['ACTION_REVISION'],
              'source_directory_input': os.environ['SOURCE_DIRECTORY'] if os.environ['EXPLICIT_SOURCE'] == 'true' else '(omitted)',
              'fixture': 'package at repository root', 'outcome': os.environ['ACTION_OUTCOME'],
              'analysis_exit': int(os.environ['exit_code']), 'selected_files': selected,
              'source_config_replaced': digest(source / '.clang-tidy') == digest(expected_config),
              'source_config_sha256': digest(source / '.clang-tidy'),
              'workspace_config_sha256': digest(workspace / '.clang-tidy'),
              'legacy_config_cache_hash': os.environ['LEGACY_CONFIG_HASH'],
              'selected_config_cache_hash': os.environ['SELECTED_CONFIG_HASH'],
              'build_at_workspace': database_path.is_file(),
              'normalized_database': json.loads(database_path.read_text().replace(str(workspace), '<WORKSPACE>')),
              'normalized_report': sorted(re.sub(r'(?<=-export-fixes )/tmp/\S+\.yaml', '<TEMP_FIXES>', report.replace('-p=build/', '-p=<BUILD>').replace(f'-p={workspace}/build', '-p=<BUILD>').replace(str(workspace), '<WORKSPACE>')).splitlines())}
    (target / 'result.json').write_text(json.dumps(result, indent=2))
    expected_files = ['checks/checked.cpp'] if supported else ['checks/checked.cpp', 'checks/ignored.cpp', 'checks/wildcard_ignored.cpp']
    assert selected == expected_files, (case, phase, selected)
    assert result['source_config_replaced'] == supported
    assert not (source / '.clang-tidy').is_symlink()
    if source != workspace:
        assert not (source / 'build').exists()
        assert not (source / 'log').exists()
    if supported and phase == 'positive':
        assert result['outcome'] == 'success' and result['analysis_exit'] == 0
    else:
        assert result['outcome'] == 'failure' and result['analysis_exit'] != 0
    if phase == 'negative':
        assert 'modernize-use-nullptr' in report
    if source == workspace:
        assert result['legacy_config_cache_hash'] == result['selected_config_cache_hash']
    print(json.dumps({key: result[key] for key in ['case', 'distro', 'phase', 'revision', 'source_directory_input',
                                                  'outcome', 'analysis_exit', 'selected_files', 'source_config_replaced']}, indent=2))


def compare(directory):
    results = [json.loads(path.read_text()) for path in directory.rglob('result.json')]
    index = {(row['distro'], row['case'], row['phase']): row for row in results}
    assert len(index) == 16, f'Expected 10 positive and 6 negative results, found {len(index)}'
    equality_keys = ['outcome', 'analysis_exit', 'selected_files', 'source_config_replaced',
                     'source_config_sha256', 'workspace_config_sha256', 'legacy_config_cache_hash',
                     'selected_config_cache_hash', 'build_at_workspace', 'normalized_database', 'normalized_report']
    rows = ['| ROS | Scenario | Upstream main | Candidate |',
            '| --- | --- | --- | --- |']
    for distro in ['humble', 'jazzy']:
        for before, after, phases in [('before-root', 'after-root', ['positive', 'negative']),
                                      ('before-nested', 'after-nested-default', ['positive'])]:
            for phase in phases:
                baseline = index[distro, before, phase]
                candidate = index[distro, after, phase]
                for key in equality_keys:
                    assert baseline[key] == candidate[key], (distro, before, after, phase, key)
                print(f'PASS {distro}: {phase} {before} and {after} have equivalent observable behavior')
        for phase in ['positive', 'negative']:
            nested = index[distro, 'after-nested', phase]
            assert nested['analysis_exit'] == (0 if phase == 'positive' else 1)
            assert nested['source_config_replaced'] and nested['selected_files'] == ['checks/checked.cpp']
            assert nested['selected_config_cache_hash'] == index[distro, 'after-root', phase]['selected_config_cache_hash']
        rows.extend([f'| {distro} | Root checkout, input omitted | Pass; bad code rejected | Same behavior |',
                     f'| {distro} | Nested checkout, input omitted | Unsupported layout | Same behavior |',
                     f'| {distro} | Nested checkout, input set | Input unavailable | Pass; bad code rejected |'])
    summary = '\n'.join(['All before/after assertions passed.', '', *rows,
                          '', 'Compared action outcomes, exit codes, selected files, downloaded configs, config cache hashes, compilation databases, and normalized reports. Report comparison accounts for equivalent build paths, random temporary fixes paths, and parallel output ordering; original reports are included.',
                          '', 'Every fixture places the ROS package at the repository root. Negative runs invoke the composite action again using explicit target files and require its final failure step to reject modernize-use-nullptr.'])
    (directory / 'summary.md').write_text(summary + '\n')
    (directory / 'summary.json').write_text(json.dumps(results, indent=2))
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write(summary + '\n')
    print(summary)


if sys.argv[1] == 'capture':
    capture(sys.argv[2])
else:
    compare(Path(sys.argv[2]))
