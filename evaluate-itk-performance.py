#!/usr/bin/env python

import argparse
import subprocess
import sys
import os
import socket
import json

parser = argparse.ArgumentParser(prog='evaluate-itk-performance')

subparsers = parser.add_subparsers(help='subcommands for individual steps',
        dest='command')

run_parser = subparsers.add_parser('run',
        help='build ITK and build and run the benchmarks')
run_parser.add_argument('src', help='ITK source directory')
run_parser.add_argument('bin', help='ITK build directory')
run_parser.add_argument('benchmark_bin',
        help='ITK performance benchmarks build directory')
run_parser.add_argument('-g', '--git-tag',
        help='ITK Git tag', default='master')

upload_parser = subparsers.add_parser('upload',
        help='upload the benchmarks to data.kitware.com')
upload_parser.add_argument('benchmark_bin',
        help='ITK performance benchmarks build directory')
upload_parser.add_argument('api_key',
        help='Your data.kitware.com API key from "My account -> API keys"')

args = parser.parse_args()

def check_for_required_programs(command):
    if command == 'run':
        try:
            subprocess.check_call(['git', '--version'], stdout=subprocess.PIPE)
        except subprocess.CalledProcessError:
            sys.stderr.write("Could not run 'git', please install Git\n")
            sys.exit(1)
        try:
            subprocess.check_call(['cmake', '--version'], stdout=subprocess.PIPE)
        except CalledProcessError:
            sys.stderr.write("Could not run 'cmake', please install CMake\n")
            sys.exit(1)
        try:
            subprocess.check_call(['ctest', '--version'], stdout=subprocess.PIPE)
        except CalledProcessError:
            sys.stderr.write("Could not run 'ctest', please install CMake\n")
            sys.exit(1)
        try:
            subprocess.check_call(['ninja', '--version'], stdout=subprocess.PIPE)
        except CalledProcessError:
            sys.stderr.write("Could not run 'ninja', please install the Ninja build tool\n")
            sys.exit(1)
    elif command == 'upload':
        try:
            import girder_client
        except ImportError:
            sys.stderr.write("Could not import girder_client, please run 'python -m pip install girder-client'\n")
            sys.exit(1)

def create_run_directories(itk_src, itk_bin, benchmark_bin, git_tag):
    if not os.path.exists(os.path.join(itk_src, '.git')):
        dirname = os.path.dirname(itk_src)
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        subprocess.check_call(['git', 'clone',
            'https://github.com/InsightSoftwareConsortium/ITK.git', itk_src])
    os.chdir(itk_src)
    # Stash any uncommited changes
    subprocess.check_call(['git', 'stash'])
    subprocess.check_call(['git', 'reset', '--hard', git_tag])

    if not os.path.exists(itk_bin):
        os.makedirs(itk_bin)

    if not os.path.exists(benchmark_bin):
        os.makedirs(benchmark_bin)

def extract_itk_information(itk_src):
    information = dict()
    information['ITK_MANUAL_BUILD_INFORMATION'] = dict()
    manual_build_info = information['ITK_MANUAL_BUILD_INFORMATION']
    os.chdir(itk_src)
    itk_git_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
    manual_build_info['GIT_CONFIG_SHA1'] = itk_git_sha
    itk_git_date = subprocess.check_output(['git', 'show', '-s', '--format=%ci',
        'HEAD']).strip()
    manual_build_info['GIT_CONFIG_DATE'] = itk_git_date
    local_modifications = subprocess.check_output(['git', 'diff', '--shortstat',
            'HEAD'])
    manual_build_info['GIT_LOCAL_MODIFICATIONS'] = local_modifications
    print(local_modifications)
    return information

def build_itk(itk_src, itk_bin):
    os.chdir(itk_bin)
    subprocess.check_call(['cmake',
        '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE:STRING=Release',
        '-DCMAKE_CXX_STANDARD:STRING=11',
        '-DBUILD_TESTING:BOOL=OFF',
        '-DBUILD_EXAMPLES:BOOL=OFF',
        itk_src])
    subprocess.check_call(['ninja'])

# fca883daf05ac62ee0449513dbd2ad30ff9591f0 is sha1 that introduces itk::BuildInformation
# so all ancestors need to prevent the benchmarking from using
def check_for_build_information(itk_src):
    os.chdir(itk_src)
    try:
        has_itkbuildinformation = bool(subprocess.check_call(['git', 'merge-base',
            '--is-ancestor', 'HEAD',
            'fca883daf05ac62ee0449513dbd2ad30ff9591f0']))
    except subprocess.CalledProcessError:
        has_itkbuildinformation = True
    return has_itkbuildinformation

def build_benchmarks(benchmark_src, benchmark_bin,
        itk_bin,
        itk_has_buildinformation):
    os.chdir(benchmark_bin)
    if itk_has_buildinformation:
        build_information_arg = '-DITK_HAS_INFORMATION_H:BOOL=ON'
    else:
        build_information_arg = '-DITK_HAS_INFORMATION_H:BOOL=OFF'
    subprocess.check_call(['cmake',
        '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE:STRING=Release',
        '-DCMAKE_CXX_STANDARD:STRING=11',
        '-DITK_DIR:PATH=' + itk_bin,
        build_information_arg,
        benchmark_src])
    subprocess.check_call(['ninja'])

def run_benchmarks(benchmark_bin, itk_information):
    os.chdir(benchmark_bin)
    subprocess.check_call(['ctest'])

def upload_benchmark_results(benchmark_bin, api_key=None):
    hostname = socket.gethostname().lower()
    results_dir = os.path.join(benchmark_bin, 'BenchmarkResults',
            hostname)
    if not os.path.exists(results_dir):
        sys.stderr.write('Expected results directory does not exist: ' + results_dir)
        sys.exit(1)
    from girder_client import GirderClient
    gc = GirderClient(apiUrl='https://data.kitware.com/api/v1')
    gc.authenticate(apiKey=api_key)
    # ITK/PerformanceBenchmarkingResults
    folder_id = '5af50c818d777f06857985e3'
    hostname_folder = gc.loadOrCreateFolder(hostname, folder_id, 'folder')
    gc.upload(os.path.join(results_dir, '*.json'), hostname_folder['_id'],
            leafFoldersAsItems=False, reuseExisting=True)

def visualize_benchmark_results(benchmark_results_dir):
    import json
	import os
	from os.path import join as pjoin
	import plotly.plotly as py
	import plotly.graph_objs as go

	DATA_DIR = './data'

	modules_performance = {}

	for filename in os.listdir(DATA_DIR):
		filename = pjoin(DATA_DIR, filename)
		with open(filename) as data_file:
			data_string = data_file.read()
			try:
				df = json.loads(data_string)
				module_name = df['Probes'][0]['Name']

				if module_name not in modules_performance:
					modules_performance[module_name] = {}

				probes_mean_time = df['Probes'][0]['Mean']
				config_date = df['ITK_MANUAL_BUILD_INFO']['GIT_CONFIG_DATE']
				timestamp = config_date, probes_mean_time

				itk_version = df['SystemInformation']['ITKVersion']

				if itk_version in modules_performance[module_name]:
					modules_performance[module_name][itk_version].append(timestamp)
				else:
					modules_performance[module_name][itk_version] = []
					modules_performance[module_name][itk_version].append(timestamp)

			except ValueError:
				print(repr(data_string))

	modules_figs = []

	for module_name, module_dict in modules_performance.items():
		for itk_version, probes in module_dict.items():
			modules_data = []
			# timestamp, probes_mean_time = zip(*probes)
			timestamp = []
			probes_mean_time = []
			for point in probes:
				timestamp.append(point[0])
				probes_mean_time.append(point[1])
			trace = go.Scatter(
				x=timestamp,
				y=probes_mean_time,
				mode='lines+markers',
				name=itk_version
			)
			modules_data.append(trace)

		layout = dict(title='ITK Module: {} <br>Performance stats'.format(module_name),
					  xaxis=dict(title='Date'),
					  yaxis=dict(title='Mean Probes Time (s)'),
					  showlegend=True
					  )
		modules_figs.append(dict(data=modules_data, layout=layout))

for module_fig in modules_figs:
    py.plot(module_fig)


check_for_required_programs(args.command)
benchmark_src = os.path.abspath(os.path.dirname(__file__))

if args.command == 'run':
    create_run_directories(args.src, args.bin,
            args.benchmark_bin,
            args.git_tag)

    print('\n\nITK Repository Information:')
    itk_information = extract_itk_information(args.src)
    print(itk_information)
    os.environ['ITKPERFORMANCEBENCHMARK_AUX_JSON'] = \
        json.dumps(itk_information)


    print('\nBuilding ITK...')
    build_itk(args.src, args.bin)

    itk_has_buildinformation = check_for_build_information(args.src)

    print('\nBuilding benchmarks...')
    build_benchmarks(benchmark_src, args.benchmark_bin, args.bin,
            itk_has_buildinformation)

    print('\nRunning benchmarks...')
    run_benchmarks(args.benchmark_bin, itk_information)

    print('\nDone running performance benchmarks.')
elif args.command == 'upload':
    upload_benchmark_results(args.benchmark_bin, args.api_key)

