#!venv/bin/python3

import os
import time
from subprocess import Popen
import re
import locale
import threading
import requests
import json
import configparser
import errno

ac_start_time = time.time()
locale.setlocale(locale.LC_ALL, '')

ac_name = "autocove"

# protects print() and global stats
ac_sem = threading.Semaphore()

ac_v = 1.6
ac_author = "dragan.stancevic@canonical.com"
ac_description = "Parallel Distributed Coverity Scanning Automaton"

ac_home_dir = "~/autocove"
ac_home_dir_x = os.path.expanduser(ac_home_dir)

ac_sources = "{}/{}".format(ac_home_dir, "sources")
ac_sources_x = os.path.expanduser(ac_sources)

ac_logs = "{}/{}".format(ac_home_dir, "logs")
ac_logs_x = os.path.expanduser(ac_logs)

ac_old_cwd = ""
ac_ts = time.strftime("%Y-%m-%d_%H:%M:%S", time.gmtime())

# stack of ROS projects we support
# "directories to test" list is filled automatically, leave empty
#   ("ros version", "project", "git location", ["directories", "to", "test"])
ac_projects = [
    ("1", "roscpp_core", "https://github.com/ros/roscpp_core.git", [])
    # ("1", "ros_comm", "https://github.com/ros/ros_comm.git", []),
    # ("1", "rosconsole", "https://github.com/ros/rosconsole.git", [])
    # ("1", "rosconsole_bridge", "https://github.com/ros/rosconsole_bridge.git", [])
    # ("1", "roslint", "https://github.com/ros/roslint.git", []),
    # ("1", "rospack", "https://github.com/ros/rospack.git", [])
    # ("1", "std_msgs", "https://github.com/ros/std_msgs.git", [])
    # ("1", "urdf", "https://github.com/ros/urdf.git", [])
    # ("1", "resource_retriever", "https://github.com/ros/resource_retriever.git", [])
    # ("1", "pluginlib", "https://github.com/ros/pluginlib.git", [])
    # ("1", "nodelet_core", "https://github.com/ros/nodelet_core.git", [])
]
# defines to make it easier to reference fields in the ac_projects tuples
Ros = 0
Proj = 1
Git = 2
Dirs = 3

ac_hosts = []
# defines to make it easier to reference fields in the ac_workers tuples
User = 0
Host = 1

# number of workers we start per remote host
ac_workers_per_host = 1
# maximum number of workers we have running at any point in time
ac_workers_max = len(ac_hosts) * ac_workers_per_host
# list of all running worker threads
ac_worker_q = []

ac_key = "~/authkey"

# statistics
ac_total_modules = 0
ac_total_defects = 0
ac_scanned_modules = []
ac_total_files = 0
ac_total_functions = 0
ac_defects_found = []
Defect = 0
Count = 1

ac_cfg_name = "{}.cfg".format(ac_name)
ac_cfg_must_have = [
    ["trello_api", "key"],
    ["trello_api", "token"],
    ["trello_variables", "labels"],
    ["trello_variables", "users"],
    ["trello_variables", "default_list"],
    ["coverity", "server_ip"],
    ["coverity", "server_port"],
    ["workers", "user"],
    ["workers", "hosts"]
]

ac_trello_cards_created = []

ac_cfg = configparser.ConfigParser()


# load our config
def ac_load_config():
    if ac_cfg.read(ac_cfg_name) != [ac_cfg_name]:
        print("error: no valid config in {}".format(ac_cfg_name))
        exit(errno.ENOENT)


# make sure none of the settings are zero
def ac_validate_config():
    for label in ac_cfg:
        for val in ac_cfg[label]:
            if ac_cfg[label][val] == "":
                print("{}->{} can't be empty".format(label, val))
                exit(errno.EINVAL)


# make sure we got the config settings we need
def ac_check_config_vals():
    for mh in ac_cfg_must_have:
        drop_out = True
        for label in ac_cfg:
            for val in ac_cfg[label]:
                if label == mh[0] and val == mh[1]:
                    drop_out = False
                    continue
        if drop_out is True:
            print("error: missing config {}->{}".format(mh[0], mh[1]))
            exit(errno.EINVAL)


# pull some values from config
def ac_populate_from_config_vals():
    global ac_hosts
    global ac_workers_max
    global ac_workers_per_host

    host_list = ac_cfg['workers']['hosts'].split(',')
    for host in host_list:
        ac_hosts.append((ac_cfg['workers']['user'], host))
    ac_workers_max = len(ac_hosts) * ac_workers_per_host


def ac_run_trello_get_my_boards():
    ac_trello_my_boards = "https://api.trello.com/1/members/me/boards"
    ac_trello_my_boards_param = {
        "fields": "name",
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    ret = requests.request("GET", ac_trello_my_boards, params=ac_trello_my_boards_param)
    val = json.loads(ret.text)
    print(val[0]['name'])
    print(ret.text)


def ac_run_trello_create_robotics_board_card(name, description):
    global ac_trello_cards_created
    ac_trello_board_robotics_cards = "https://api.trello.com/1/cards"
    ac_trello_board_robotics_param = {
        "name": name,
        "idList": ac_cfg['trello_variables']['default_list'],
        "desc": description,
        "pos": "top",
        "keepFromSource": "all",
        "idMembers": "{}".format(ac_cfg['trello_variables']['users']),
        "idLabels": "{}".format(ac_cfg['trello_variables']['labels']),
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    ret = requests.request("POST", ac_trello_board_robotics_cards, params=ac_trello_board_robotics_param)
    val = json.loads(ret.text)
    ac_trello_cards_created.append((val['id'], val['name']))
    return val['id']


def ac_run_trello_get_robotics_cards_list(trello_id):
    ac_trello_board_robotics_cards_list = "https://api.trello.com/1/lists/{}/cards".format(trello_id)
    ac_trello_board_robotics_param = {
        "fields": "name,id",
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    ret = requests.request("GET", ac_trello_board_robotics_cards_list, params=ac_trello_board_robotics_param)
    return json.loads(ret.text)


def ac_run_trello_delete_robotics_board_card(trello_id):
    ac_trello_board_robotics_card_delete = "https://api.trello.com/1/cards/{}".format(trello_id)
    ac_trello_board_robotics_param = {
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    requests.request("DELETE", ac_trello_board_robotics_card_delete, params=ac_trello_board_robotics_param)


def ac_run_trello_robotics_board_card_attach(trello_id, name, path):
    ac_trello_board_robotics_card_attach = "https://api.trello.com/1/cards/{}/attachments".format(trello_id)
    ac_trello_board_robotics_param = {
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token'],
        "mimeType": "text/plain",
    }
    files = {
        "file": (name, open(path, 'rb'))
    }

    requests.request("POST", ac_trello_board_robotics_card_attach, params=ac_trello_board_robotics_param, files=files)


def ac_run_trello_robotics_board_card_comment(trello_id, comment):
    ac_trello_board_robotics_card_comment = "https://api.trello.com/1/cards/{}/actions/comments".format(trello_id)
    ac_trello_board_robotics_param = {
        "text": comment,
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token'],
    }
    requests.request("POST", ac_trello_board_robotics_card_comment, params=ac_trello_board_robotics_param)


def ac_run_trello_get_robotics_board_labels():
    ac_trello_board_robotics_id = "5bd9e6e90793cc70138a69f6"
    ac_trello_board_robotics_labels = "https://api.trello.com/1/boards/{}/labels".format(ac_trello_board_robotics_id)
    ac_trello_board_robotics_param = {
        "fields": "name",
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    requests.request("GET", ac_trello_board_robotics_labels, params=ac_trello_board_robotics_param)


def ac_run_trello_get_robotics_board_members():
    ac_trello_board_robotics_id = "5bd9e6e90793cc70138a69f6"
    ac_trello_board_robotics_members = "https://api.trello.com/1/boards/{}/members".format(ac_trello_board_robotics_id)
    ac_trello_board_robotics_param = {
        "cards": "none",
        "card_fields": "id",
        "filter": "open",
        "fields": "all",
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    requests.request("GET", ac_trello_board_robotics_members, params=ac_trello_board_robotics_param)


def ac_run_trello_get_robotics_board_lists():
    ac_trello_board_robotics_id = "5bd9e6e90793cc70138a69f6"
    ac_trello_board_robotics_lists = "https://api.trello.com/1/boards/{}/lists".format(ac_trello_board_robotics_id)
    ac_trello_board_robotics_param = {
        "key": ac_cfg['trello_api']['key'],
        "token": ac_cfg['trello_api']['token']
    }
    requests.request("GET", ac_trello_board_robotics_lists, params=ac_trello_board_robotics_param)


def ac_dump_about(to_print):
    line = "AutoCove v{} by {} - Run {}".format(ac_v, ac_author, ac_ts)
    if to_print is True:
        ac_sem.acquire()
        print("{}\r".format(line), flush=True)
        ac_sem.release()
        return line
    else:
        return line


def ac_make_dirs():
    try:
        os.makedirs(ac_sources_x)
    except FileExistsError:
        # directory already exists
        pass
    try:
        os.makedirs("{}/ros1".format(ac_sources_x))
    except FileExistsError:
        # directory already exists
        pass
    try:
        os.makedirs("{}/ros2".format(ac_sources_x))
    except FileExistsError:
        # directory already exists
        pass

    try:
        os.makedirs(ac_logs_x)
    except FileExistsError:
        # directory already exists
        pass
    try:
        os.makedirs("{}/ros1".format(ac_logs_x))
    except FileExistsError:
        # directory already exists
        pass
    try:
        os.makedirs("{}/ros2".format(ac_logs_x))
    except FileExistsError:
        # directory already exists
        pass


def ac_trim_finished_workers(wait):
    global ac_worker_q
    if wait is True:
        for t in ac_worker_q:
            t.join()
        # reset the queue as we waited for all of them
        ac_worker_q = []
    else:
        for t in ac_worker_q:
            if t.is_alive() == False:
                ac_worker_q.remove(t)
                t.join()


def ac_go_home(path):
    global ac_old_cwd
    try:
        os.makedirs(path)
    except FileExistsError:
        # directory already exists
        pass
    # save the old working directory
    ac_old_cwd = os.getcwd()
    # go to our default working directory
    os.chdir(path)


def ac_dump_all_modules(modules):
    line = ""
    for m in modules:
        if len(line) == 0:
            line = "{}".format(m)
        elif (len(line) + len(m)) < 80:
            line = "{} {}".format(line, m)
        else:
            print("{}\r".format(line), flush=True)
            line = "{}".format(m)
    print("{}\r".format(line), flush=True)


def ac_line_print_text(msg, delim):
    cnt = (80 - 2 - len(msg)) / 2
    delims = delim * int(cnt)
    line = "{}={}={}".format(delims, msg, delims)
    pad = 80 - len(line)
    line = line + (pad * delim)
    print("{}\r".format(line), flush=True)


def ac_return():
    global ac_old_cwd
    ac_sem.acquire()
    print("\r", flush=True)
    about = ac_dump_about(False)
    ac_line_print_text(about, "-")
    ac_line_print_text(ac_description, " ")
    ac_line_print_text("Modules", "-")
    ac_dump_all_modules(ac_scanned_modules)
    print("{}\r".format("-" * 80), flush=True)

    tm = locale.format("%d", ac_total_modules, grouping=True)
    tf = locale.format("%d", ac_total_files, grouping=True)
    tff = locale.format("%d", ac_total_functions, grouping=True)
    td = locale.format("%d", ac_total_defects, grouping=True)

    print("Functions Scanned:\t{}\r\nFiles Scanned:\t\t{}\r".format(tff, tf), flush=True)
    print("Modules Scanned:\t{}\r\nDefects Found:\t\t{}\r".format(tm, td), flush=True)

    ac_line_print_text("Defects", "-")
    ac_dump_defects()
    ac_line_print_text("Time", "-")
    print("Start to Finish: {:.0f} seconds\r".format(time.time() - ac_start_time), flush=True)
    print("{}\r".format("=" * 80), flush=True)
    print(ac_trello_cards_created, flush=True)
    print("\r".format("=" * 80), flush=True)

    ac_sem.release()
    # return to previous working directory
    os.chdir(ac_old_cwd)


# check if we have the git tree of the project
def ac_check_for_project_git(p):
    git_path = "{}/ros{}/{}/.git/config".format(ac_sources_x, p[Ros], p[Proj])
    return os.path.isfile(git_path)


# clone git sources for the project locally
def ac_fetch_project_git(p):
    fetch_path = "{}/ros{}".format(ac_sources_x, p[Ros])
    os.chdir(fetch_path)
    os.system("git clone " + p[Git])
    os.chdir(ac_home_dir_x)


def ac_check_for_local_sources(pstack):
    projects = pstack.copy()
    while projects != []:
        p = projects.pop()
        if ac_check_for_project_git(p) == False:
            ac_fetch_project_git(p)


def ac_distribute_sources_to_hosts(pstack, hstack):
    projects = pstack.copy()
    hosts = hstack.copy()
    while hosts != []:
        h = hosts.pop()
        while projects != []:
            p = projects.pop()
            ac_distribute_source_to_host(p, h)


def ac_distribute_source_to_host(p, h):
    ac_sem.acquire()
    print("distributing \"{}\" to: {}@{}\r".format(p[Proj], h[User], h[Host]), flush=True)
    ac_sem.release()

    git_path = "{}/ros{}/{}".format(ac_sources_x, p[Ros], p[Proj])
    destination = "{}/ros{}/{}".format(ac_sources, p[Ros], p[Proj])

    mkdir = "ssh {}@{} \"mkdir -p {}\"".format(h[User], h[Host], destination)
    os.system(mkdir)

    rsync = "rsync -ah --delete {}/ {}@{}:{}".format(git_path, h[User], h[Host], destination)
    os.system(rsync)


def ac_enumerate_project_subdirs(projects):
    for p in projects:
        git_path = "{}/ros{}/{}".format(ac_sources_x, p[Ros], p[Proj])
        ac_find_package_files(p, "package.xml", git_path)


def ac_find_package_files(p, name, location):
    ac_sem.acquire()
    print("scanning {}\r".format(p[Proj]), flush=True)
    ac_sem.release()
    for root, dirs, files in os.walk(location):
        for fname in files:
            if fname != name:
                continue
            if location == root:
                m = "."
                package = location.split("/")[-1]
            else:
                m = root.replace("{}/".format(location), "")
                package = m
            p[Dirs].append(m)
            ac_sem.acquire()
            print("\tfound package: {}\r".format(package), flush=True)
            ac_sem.release()


def ac_tally_modules_and_defects(mod, log):
    global ac_scanned_modules
    global ac_total_modules

    # trim parent
    m = mod.split('/')
    ac_sem.acquire()
    ac_scanned_modules.append(m[-1])
    ac_total_modules += 1
    ac_sem.release()
    (attach, summary) = ac_extract_log_values(log)
    return (attach, summary)


def ac_extract_log_values(path):
    global ac_total_files
    global ac_total_functions
    global ac_total_defects
    attach = False

    summary = ""
    with open(path, 'r') as log:
        for line in log:
            l = line.strip()

            m = re.match("^Files analyzed\ +: (\d+)$", l)
            if m:
                ac_sem.acquire()
                ac_total_files += int(m.group(1))
                ac_sem.release()
                summary += "{}\n".format(l)
                continue

            m = re.match("^Functions analyzed\ +: (\d+)$", l)
            if m:
                ac_sem.acquire()
                ac_total_functions += int(m.group(1))
                ac_sem.release()
                summary += "{}\n".format(l)
                continue

            # coverity prints different totals, so we capture them all
            m = re.match("^Defect occurrences found\ +: (\d+) Total$", l)
            if m:
                ac_sem.acquire()
                ac_total_defects += int(m.group(1))
                ac_sem.release()
                summary += "{}\n".format(l)
                if int(m.group(1)) > 0:
                    attach = True
                continue

            # coverity prints different totals, so we capture them all
            m = re.match("^Defect occurrences found\ +: (\d+)$", l)
            if m:
                ac_sem.acquire()
                ac_total_defects += int(m.group(1))
                ac_sem.release()
                summary += "{}\n".format(l)
                if int(m.group(1)) > 0:
                    attach = True
                continue

            # coverity prints different totals, so we capture them all
            m = re.match("^Defect occurrences found\ +: (\d+) (\w+)$", l)
            if m:
                ac_sem.acquire()
                ac_total_defects += int(m.group(1))
                ac_sem.release()
                summary += "{}\n".format(l)
                ac_add_log_value(m.group(2), int(m.group(1)))
                if int(m.group(1)) > 0:
                    attach = True
                continue

            m = re.match("^(\d+) (\w+)$", l)
            if m:
                ac_sem.acquire()
                ac_add_log_value(m.group(2), int(m.group(1)))
                ac_sem.release()
                summary += "{}\n".format(l)
                continue

        log.close()
        return (attach, summary)


def ac_add_log_value(label, count):
    global ac_defects_found

    i = 0
    for l in ac_defects_found:
        if l[Defect] == label:
            new_count = l[Count] + count
            ac_defects_found[i] = (label, new_count)
            return
        i += 1
    ac_defects_found.append((label, count))


def ac_dump_defects():
    global ac_defects_found

    for d in ac_defects_found:
        print("{:5} - {}\r".format(d[Count], d[Defect]), flush=True)


def ac_amalgamate_project_and_modules_name(p, e):
    name = p[Proj]
    if e == ".":
        return name
    names = e.split('/')
    for n in names:
        tmp = "{}-{}".format(name, n)
        name = tmp
    return name


def ac_remote_run_and_logit(i, cmd, path, label, w, p, e):
    full_name = ac_amalgamate_project_and_modules_name(p, e)
    logpath = "{}/ros{}/{}_{}.{}.txt".format(ac_logs_x, p[Ros], full_name, ac_ts, label)
    if path == "":
        ssh = "ssh {}@{} 'bash -lc \"{}\"'".format(w[User], w[Host], cmd)
    else:
        ssh = "ssh {}@{} 'bash -lc \"cd {}; {}\"'".format(w[User], w[Host], path, cmd)
    ac_sem.acquire()
    print("#{} - running {} on {}@{}\r\n\tlog at: {}\r".format(i, label, w[User], w[Host], logpath), flush=True)
    ac_sem.release()
    log = open(logpath, 'w')
    p = Popen(ssh, shell=True, universal_newlines=True, stdout=log, stderr=log)
    p.wait()
    log.flush()
    log.close()
    return logpath


def ac_run_mkdirs(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build/{}".format(ac_sources, p[Ros], p[Proj], e, ac_name)
    mk_tmp = "mkdir -p {}".format(destination)
    ac_remote_run_and_logit(i, mk_tmp, "", "mkdir", w, p, e)


def ac_run_cmake(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    cmake = "cmake .."
    ac_remote_run_and_logit(i, cmake, destination, "cmake", w, p, e)


def ac_run_make(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    make = "cov-build --dir {} make".format(ac_name)
    ac_remote_run_and_logit(i, make, destination, "make", w, p, e)


def ac_run_capture_python(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    getpy = "cov-build --dir {} --no-command --fs-capture-search ./".format(ac_name)
    ac_remote_run_and_logit(i, getpy, destination, "python-capture", w, p, e)


def ac_run_analyze(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    analyze = "cov-analyze --dir {} --all --disable-parse-warnings --enable-constraint-fpp --max-mem 256 --jobs 2 --aggressiveness-level high --strip-path {}".format(ac_name, destination)
    log = ac_remote_run_and_logit(i, analyze, destination, "cov-analyze", w, p, e)
    (attach, summary) = ac_tally_modules_and_defects(e, log)
    return (attach, summary)


def ac_run_emacs(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    emacs = "cov-format-errors --emacs-style --dir {}".format(ac_name)
    log_path = ac_remote_run_and_logit(i, emacs, destination, "cov-format-errors", w, p, e)
    return log_path


def ac_run_create_coverity_server_projects_and_streams(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    project = "ac-ros{}-{}".format(p[Ros], p[Proj])
    desc = "{}-created-by-{}".format(project, "AutoCove")
    full_name = ac_amalgamate_project_and_modules_name(p, e)
    stream = "ac-ros{}-{}".format(p[Ros], full_name)
    srv = ac_cfg['coverity']['server_ip']
    port = ac_cfg['coverity']['server_port']

    create_proj = "cov-manage-im --host {} --port {} --auth-key-file {} --mode projects --add --set name:{} --set description:{}".format(srv, port, ac_key, project, desc)
    ac_remote_run_and_logit(i, create_proj, destination, "cov-manage-im_create_proj", w, p, e)

    create_stream = "cov-manage-im --host {} --port {} --auth-key-file {} --mode streams --add --set name:{} --set lang:mixed".format(srv, port, ac_key, stream)
    ac_remote_run_and_logit(i, create_stream, destination, "cov-manage-im_create_stream", w, p, e)

    associate_stream = "cov-manage-im --host {} --port {} --auth-key-file {} --mode projects --update --name {} --insert stream:{}".format(srv, port, ac_key, project, stream)
    ac_remote_run_and_logit(i, associate_stream, destination, "cov-manage-im_associate_stream", w, p, e)


def ac_run_create_trello_card(i, w, p, e):
    full_name = ac_amalgamate_project_and_modules_name(p, e)
    name = "ac-ros{}-{}_{}".format(p[Ros], full_name, ac_ts)
    description = "{}\nstarting scan on {}".format(ac_dump_about(False), name)
    ac_sem.acquire()
    print("#{} - creating trello card {} on {}\r".format(i, name, "https://trello.com"), flush=True)
    ac_sem.release()
    card_id = ac_run_trello_create_robotics_board_card(name, description)
    return card_id


def ac_run_upload_to_coverity_server(i, w, p, e):
    destination = "{}/ros{}/{}/{}/build".format(ac_sources, p[Ros], p[Proj], e)
    full_name = ac_amalgamate_project_and_modules_name(p, e)
    stream = "ac-ros{}-{}".format(p[Ros], full_name)
    srv = ac_cfg['coverity']['server_ip']
    port = ac_cfg['coverity']['server_port']
    upload = "cov-commit-defects --host {} --port {} --auth-key-file {}  --dir {} --stream {}".format(srv, port, ac_key, ac_name, stream)
    ac_remote_run_and_logit(i, upload, destination, "cov-commit-defects", w, p, e)


def ac_worker_thread(i, w, p, e):
    ac_sem.acquire()
    print("start - worker #{} - {}@{}\r".format(i, w[User], w[Host]), flush=True)
    ac_sem.release()
    card_id = ac_run_create_trello_card(i, w, p, e)
    ac_run_mkdirs(i, w, p, e)
    ac_run_cmake(i, w, p, e)
    ac_run_make(i, w, p, e)
    ac_run_capture_python(i, w, p, e)
    (attach, summary) = ac_run_analyze(i, w, p, e)
    ac_run_trello_robotics_board_card_comment(card_id, summary)
    if attach is True:
        log_path = ac_run_emacs(i, w, p, e)
        log_name = log_path.split('/')
        ac_run_trello_robotics_board_card_attach(card_id, log_name[-1], log_path)
    ac_run_create_coverity_server_projects_and_streams(i, w, p, e)
    ac_run_upload_to_coverity_server(i, w, p, e)
    ac_sem.acquire()
    print("stop - worker #{} - {}@{}\r".format(i, w[User], w[Host]), flush=True)
    print("\r", flush=True)
    ac_sem.release()


def ac_run_workers(projects):
    i = 0
    j = 0
    for p in projects:
        for e in p[Dirs]:
            w = ac_hosts[j]
            wait_to_run = True
            while wait_to_run is True:
                ac_trim_finished_workers(False)
                if len(ac_worker_q) < ac_workers_max:
                    # get new worker id
                    i += 1
                    t = threading.Thread(target=ac_worker_thread, args=(i, w, p, e))
                    t.start()
                    ac_worker_q.append(t)
                    wait_to_run = False
                    # pick the next host
                    j += 1
                    j %= len(ac_hosts)
                else:
                    wait_to_run = True
                    time.sleep(0.5)
    ac_trim_finished_workers(True)
    ac_return()


ac_dump_about(True)
ac_load_config()
ac_validate_config()
ac_check_config_vals()
ac_populate_from_config_vals()
ac_go_home(ac_home_dir_x)
ac_make_dirs()
ac_check_for_local_sources(ac_projects)
ac_distribute_sources_to_hosts(ac_projects, ac_hosts)
ac_enumerate_project_subdirs(ac_projects)
ac_run_workers(ac_projects)
