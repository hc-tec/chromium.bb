# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Module containing the various individual commands a builder can run."""

from __future__ import print_function

import base64
import collections
import datetime
import fnmatch
import glob
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile

from chromite.lib import config_lib
from chromite.lib import constants
from chromite.lib import failures_lib
from chromite.cbuildbot import swarming_lib
from chromite.cbuildbot import topology
from chromite.cli.cros.tests import cros_vm_test
from chromite.lib import cros_build_lib
from chromite.lib import cros_logging as logging
from chromite.lib import git
from chromite.lib import gob_util
from chromite.lib import gs
from chromite.lib import locking
from chromite.lib import osutils
from chromite.lib import parallel
from chromite.lib import path_util
from chromite.lib import portage_util
from chromite.lib import retry_util
from chromite.lib import timeout_util
from chromite.scripts import pushimage

site_config = config_lib.GetConfig()


_PACKAGE_FILE = '%(buildroot)s/src/scripts/cbuildbot_package.list'
CHROME_KEYWORDS_FILE = ('/build/%(board)s/etc/portage/package.keywords/chrome')
CHROME_UNMASK_FILE = ('/build/%(board)s/etc/portage/package.unmask/chrome')
_CROS_ARCHIVE_URL = 'CROS_ARCHIVE_URL'
_FACTORY_SHIM = 'factory_shim'
_AUTOTEST_RPC_CLIENT = ('/b/build_internal/scripts/slave-internal/autotest_rpc/'
                        'autotest_rpc_client.py')
_AUTOTEST_RPC_HOSTNAME = 'master2'
_LOCAL_BUILD_FLAGS = ['--nousepkg', '--reuse_pkgs_from_local_boards']
UPLOADED_LIST_FILENAME = 'UPLOADED'
STATEFUL_FILE = 'stateful.tgz'
# For sorting through VM test results.
_TEST_REPORT_FILENAME = 'test_report.log'
_TEST_PASSED = 'PASSED'
_TEST_FAILED = 'FAILED'
# For swarming proxy
_SWARMING_ADDITIONAL_TIMEOUT = 60 * 60
_DEFAULT_HWTEST_TIMEOUT_MINS = 1440
_SWARMING_EXPIRATION = 20 * 60
_RUN_SUITE_PATH = '/usr/local/autotest/site_utils/run_suite.py'
_ABORT_SUITE_PATH = '/usr/local/autotest/site_utils/abort_suite.py'
_MAX_HWTEST_CMD_RETRY = 10
# Be very careful about retrying suite creation command as
# it may create multiple suites.
_MAX_HWTEST_START_CMD_RETRY = 1
# For json_dump json dictionary marker
JSON_DICT_START = '#JSON_START#'
JSON_DICT_END = '#JSON_END#'


# =========================== Command Helpers =================================

def RunBuildScript(buildroot, cmd, chromite_cmd=False, **kwargs):
  """Run a build script, wrapping exceptions as needed.

  This wraps RunCommand(cmd, cwd=buildroot, **kwargs), adding extra logic to
  help determine the cause of command failures.
    - If a package fails to build, a PackageBuildFailure exception is thrown,
      which lists exactly which packages failed to build.
    - If the command fails for a different reason, a BuildScriptFailure
      exception is thrown.

  We detect what packages failed to build by creating a temporary status file,
  and passing that status file to parallel_emerge via the
  PARALLEL_EMERGE_STATUS_FILE variable.

  Args:
    buildroot: The root of the build directory.
    cmd: The command to run.
    chromite_cmd: Whether the command should be evaluated relative to the
      chromite/bin subdir of the |buildroot|.
    kwargs: Optional args passed to RunCommand; see RunCommand for specifics.
      In addition, if 'sudo' kwarg is True, SudoRunCommand will be used.
  """
  assert not kwargs.get('shell', False), 'Cannot execute shell commands'
  kwargs.setdefault('cwd', buildroot)
  enter_chroot = kwargs.get('enter_chroot', False)
  sudo = kwargs.pop('sudo', False)

  if chromite_cmd:
    cmd = cmd[:]
    cmd[0] = os.path.join(buildroot, constants.CHROMITE_BIN_SUBDIR, cmd[0])
    if enter_chroot:
      cmd[0] = path_util.ToChrootPath(cmd[0])

  # If we are entering the chroot, create status file for tracking what
  # packages failed to build.
  chroot_tmp = os.path.join(buildroot, 'chroot', 'tmp')
  status_file = None
  with cros_build_lib.ContextManagerStack() as stack:
    if enter_chroot and os.path.exists(chroot_tmp):
      kwargs['extra_env'] = (kwargs.get('extra_env') or {}).copy()
      status_file = stack.Add(tempfile.NamedTemporaryFile, dir=chroot_tmp)
      kwargs['extra_env'][constants.PARALLEL_EMERGE_STATUS_FILE_ENVVAR] = \
          path_util.ToChrootPath(status_file.name)
    runcmd = cros_build_lib.RunCommand
    if sudo:
      runcmd = cros_build_lib.SudoRunCommand
    try:
      return runcmd(cmd, **kwargs)
    except cros_build_lib.RunCommandError as ex:
      # Print the original exception.
      logging.error('\n%s', ex)

      # Check whether a specific package failed. If so, wrap the exception
      # appropriately. These failures are usually caused by a recent CL, so we
      # don't ever treat these failures as flaky.
      if status_file is not None:
        status_file.seek(0)
        failed_packages = status_file.read().split()
        if failed_packages:
          raise failures_lib.PackageBuildFailure(ex, cmd[0], failed_packages)

      # Looks like a generic failure. Raise a BuildScriptFailure.
      raise failures_lib.BuildScriptFailure(ex, cmd[0])


def ValidateClobber(buildroot):
  """Do due diligence if user wants to clobber buildroot.

  Args:
    buildroot: buildroot that's potentially clobbered.

  Returns:
    True if the clobber is ok.
  """
  cwd = os.path.dirname(os.path.realpath(__file__))
  if cwd.startswith(buildroot):
    cros_build_lib.Die('You are trying to clobber this chromite checkout!')

  if buildroot == '/':
    cros_build_lib.Die('Refusing to clobber your system!')

  if os.path.exists(buildroot):
    return cros_build_lib.BooleanPrompt(default=False)
  return True


# =========================== Main Commands ===================================


def BuildRootGitCleanup(buildroot, prune_all=False):
  """Put buildroot onto manifest branch. Delete branches created on last run.

  Args:
    buildroot: buildroot to clean up.
    prune_all: If True, prune all loose objects regardless of gc.pruneExpire.
  """
  lock_path = os.path.join(buildroot, '.clean_lock')
  deleted_objdirs = multiprocessing.Event()

  def RunCleanupCommands(project, cwd):
    with locking.FileLock(lock_path, verbose=False).read_lock() as lock:
      # Calculate where the git repository is stored.
      relpath = os.path.relpath(cwd, buildroot)
      projects_dir = os.path.join(buildroot, '.repo', 'projects')
      project_objects_dir = os.path.join(buildroot, '.repo', 'project-objects')
      repo_git_store = '%s.git' % os.path.join(projects_dir, relpath)
      repo_obj_store = '%s.git' % os.path.join(project_objects_dir, project)

      try:
        if os.path.isdir(cwd):
          git.CleanAndDetachHead(cwd)

        if os.path.isdir(repo_git_store):
          git.GarbageCollection(repo_git_store, prune_all=prune_all)
      except cros_build_lib.RunCommandError as e:
        result = e.result
        logging.PrintBuildbotStepWarnings()
        logging.warning('\n%s', result.error)

        # If there's no repository corruption, just delete the index.
        corrupted = git.IsGitRepositoryCorrupted(repo_git_store)
        lock.write_lock()
        logging.warning('Deleting %s because %s failed', cwd, result.cmd)
        osutils.RmDir(cwd, ignore_missing=True, sudo=True)
        if corrupted:
          # Looks like the object dir is corrupted. Delete the whole repository.
          deleted_objdirs.set()
          for store in (repo_git_store, repo_obj_store):
            logging.warning('Deleting %s as well', store)
            osutils.RmDir(store, ignore_missing=True)

      # TODO: Make the deletions below smarter. Look to see what exists, instead
      # of just deleting things we think might be there.

      # Delete all branches created by cbuildbot.
      if os.path.isdir(repo_git_store):
        cmd = ['branch', '-D'] + list(constants.CREATED_BRANCHES)
        # Ignore errors, since we delete branches without checking existence.
        git.RunGit(repo_git_store, cmd, error_code_ok=True)

      if os.path.isdir(cwd):
        # Above we deleted refs/heads/<branch> for each created branch, now we
        # need to delete the bare ref <branch> if it was created somehow.
        for ref in constants.CREATED_BRANCHES:
          # Ignore errors, since we delete branches without checking existence.
          git.RunGit(cwd, ['update-ref', '-d', ref], error_code_ok=True)


  # Cleanup all of the directories.
  dirs = [[attrs['name'], os.path.join(buildroot, attrs['path'])] for attrs in
          git.ManifestCheckout.Cached(buildroot).ListCheckouts()]
  parallel.RunTasksInProcessPool(RunCleanupCommands, dirs)

  # repo shares git object directories amongst multiple project paths. If the
  # first pass deleted an object dir for a project path, then other repositories
  # (project paths) of that same project may now be broken. Do a second pass to
  # clean them up as well.
  if deleted_objdirs.is_set():
    parallel.RunTasksInProcessPool(RunCleanupCommands, dirs)


def CleanUpMountPoints(buildroot):
  """Cleans up any stale mount points from previous runs."""
  # Scrape it from /proc/mounts since it's easily accessible;
  # additionally, unmount in reverse order of what's listed there
  # rather than trying a reverse sorting; it's possible for
  # mount /z /foon
  # mount /foon/blah -o loop /a
  # which reverse sorting cannot handle.
  buildroot = os.path.realpath(buildroot).rstrip('/') + '/'
  mounts = [mtab.destination for mtab in osutils.IterateMountPoints() if
            mtab.destination.startswith(buildroot)]

  for mount_pt in reversed(mounts):
    osutils.UmountDir(mount_pt, lazy=True, cleanup=False)


def WipeOldOutput(buildroot):
  """Wipes out build output directory.

  Args:
    buildroot: Root directory where build occurs.
    board: Delete image directories for this board name.
  """
  image_dir = os.path.join(buildroot, 'src', 'build', 'images')
  osutils.RmDir(image_dir, ignore_missing=True, sudo=True)


def MakeChroot(buildroot, replace, use_sdk, chrome_root=None, extra_env=None):
  """Wrapper around make_chroot."""
  cmd = ['cros_sdk', '--buildbot-log-version']
  cmd.append('--create' if use_sdk else '--bootstrap')

  if replace:
    cmd.append('--replace')

  if chrome_root:
    cmd.append('--chrome_root=%s' % chrome_root)

  RunBuildScript(buildroot, cmd, chromite_cmd=True, extra_env=extra_env)


def RunChrootUpgradeHooks(buildroot, chrome_root=None, extra_env=None):
  """Run the chroot upgrade hooks in the chroot.

  Args:
    buildroot: Root directory where build occurs.
    chrome_root: The directory where chrome is stored.
    extra_env: A dictionary of environment variables to set.
  """
  chroot_args = []
  if chrome_root:
    chroot_args.append('--chrome_root=%s' % chrome_root)

  RunBuildScript(buildroot, ['./run_chroot_version_hooks'], enter_chroot=True,
                 chroot_args=chroot_args, extra_env=extra_env)


def SetSharedUserPassword(buildroot, password):
  """Wrapper around set_shared_user_password.sh"""
  if password is not None:
    cmd = ['./set_shared_user_password.sh', password]
    RunBuildScript(buildroot, cmd, enter_chroot=True)
  else:
    passwd_file = os.path.join(buildroot, 'chroot/etc/shared_user_passwd.txt')
    osutils.SafeUnlink(passwd_file, sudo=True)


def UpdateChroot(buildroot, usepkg, toolchain_boards=None, extra_env=None):
  """Wrapper around update_chroot.

  Args:
    buildroot: The buildroot of the current build.
    usepkg: Whether to use binary packages when setting up the toolchain.
    toolchain_boards: List of boards to always include.
    extra_env: A dictionary of environmental variables to set during generation.
  """
  cmd = ['./update_chroot']

  if not usepkg:
    cmd.extend(['--nousepkg'])

  if toolchain_boards:
    cmd.extend(['--toolchain_boards', ','.join(toolchain_boards)])

  RunBuildScript(buildroot, cmd, extra_env=extra_env, enter_chroot=True)


def SetupBoard(buildroot, board, usepkg, chrome_binhost_only=False,
               extra_env=None, force=False, profile=None, chroot_upgrade=True,
               chroot_args=None):
  """Wrapper around setup_board.

  Args:
    buildroot: The buildroot of the current build.
    board: The board to set up.
    usepkg: Whether to use binary packages when setting up the board.
    chrome_binhost_only: If set, only use binary packages on the board for
      Chrome itself.
    extra_env: A dictionary of environmental variables to set during generation.
    force: Whether to remove the board prior to setting it up.
    profile: The profile to use with this board.
    chroot_upgrade: Whether to update the chroot. If the chroot is already up to
      date, you can specify chroot_upgrade=False.
    chroot_args: The args to the chroot.
  """
  cmd = ['./setup_board', '--board=%s' % board,
         '--accept_licenses=@CHROMEOS']

  # This isn't the greatest thing, but emerge's dependency calculation
  # isn't the speediest thing, so let callers skip this step when they
  # know the system is up-to-date already.
  if not chroot_upgrade:
    cmd.append('--skip_chroot_upgrade')

  if profile:
    cmd.append('--profile=%s' % profile)

  if not usepkg:
    cmd.extend(_LOCAL_BUILD_FLAGS)

  if chrome_binhost_only:
    cmd.append('--chrome_binhost_only')

  if force:
    cmd.append('--force')

  RunBuildScript(buildroot, cmd, extra_env=extra_env, enter_chroot=True,
                 chroot_args=chroot_args)


class MissingBinpkg(failures_lib.StepFailure):
  """Error class for when we are missing an essential binpkg."""


def VerifyBinpkg(buildroot, board, pkg, packages, extra_env=None):
  """Verify that an appropriate binary package exists for |pkg|.

  Using the depgraph from |packages|, check to see if |pkg| would be pulled in
  as a binary or from source.  If |pkg| isn't installed at all, then ignore it.

  Args:
    buildroot: The buildroot of the current build.
    board: The board to set up.
    pkg: The package to look for.
    packages: The list of packages that get installed on |board|.
    extra_env: A dictionary of environmental variables to set.

  Raises:
    If the package is found and is built from source, raise MissingBinpkg.
    If the package is not found, or it is installed from a binpkg, do nothing.
  """
  cmd = ['emerge-%s' % board, '-pegNvq', '--with-bdeps=y',
         '--color=n'] + list(packages)
  result = RunBuildScript(buildroot, cmd, capture_output=True,
                          enter_chroot=True, extra_env=extra_env)
  pattern = r'^\[(ebuild|binary).*%s' % re.escape(pkg)
  m = re.search(pattern, result.output, re.MULTILINE)
  if m and m.group(1) == 'ebuild':
    logging.info('(output):\n%s', result.output)
    msg = 'Cannot find prebuilts for %s on %s' % (pkg, board)
    raise MissingBinpkg(msg)


def RunBinhostTest(buildroot, incremental=True):
  """Test prebuilts for all boards, making sure everybody gets Chrome prebuilts.

  Args:
    buildroot: The buildroot of the current build.
    incremental: If True, run the incremental compatibility test.
  """
  cmd = ['../cbuildbot/binhost_test', '--log-level=debug']

  # Non incremental tests are listed in a special test suite.
  if not incremental:
    cmd += ['NoIncremental']
  RunBuildScript(buildroot, cmd, chromite_cmd=True, enter_chroot=True)


def RunBranchUtilTest(buildroot, version):
  """Tests that branch-util works at the given manifest version."""
  with osutils.TempDir() as tempdir:
    cmd = [
        'cbuildbot',
        'branch-util',
        '--local',
        '--skip-remote-push',
        '--branch-name', 'test_branch',
        '--version', version,
        '--buildroot', tempdir,
        '--no-buildbot-tags',
    ]
    RunBuildScript(buildroot, cmd, chromite_cmd=True)


def UpdateBinhostJson(buildroot):
  """Test prebuilts for all boards, making sure everybody gets Chrome prebuilts.

  Args:
    buildroot: The buildroot of the current build.
  """
  cmd = ['../cbuildbot/update_binhost_json']
  RunBuildScript(buildroot, cmd, chromite_cmd=True, enter_chroot=True)


def Build(buildroot, board, build_autotest, usepkg, chrome_binhost_only,
          packages=(), skip_chroot_upgrade=True, noworkon=False,
          extra_env=None, chrome_root=None, noretry=False,
          chroot_args=None):
  """Wrapper around build_packages.

  Args:
    buildroot: The buildroot of the current build.
    board: The board to set up.
    build_autotest: Whether to build autotest-related packages.
    usepkg: Whether to use binary packages.
    chrome_binhost_only: If set, only use binary packages on the board for
      Chrome itself.
    packages: Tuple of specific packages we want to build. If empty,
      build_packages will calculate a list of packages automatically.
    skip_chroot_upgrade: Whether to skip the chroot update. If the chroot is
      not yet up to date, you should specify skip_chroot_upgrade=False.
    noworkon: If set, don't force-build workon packages.
    extra_env: A dictionary of environmental variables to set during generation.
    chrome_root: The directory where chrome is stored.
    noretry: Do not retry package failures.
    chroot_args: The args to the chroot.
  """
  cmd = ['./build_packages', '--board=%s' % board,
         '--accept_licenses=@CHROMEOS', '--withdebugsymbols']

  if not build_autotest:
    cmd.append('--nowithautotest')

  if skip_chroot_upgrade:
    cmd.append('--skip_chroot_upgrade')

  if not usepkg:
    cmd.extend(_LOCAL_BUILD_FLAGS)

  if chrome_binhost_only:
    cmd.append('--chrome_binhost_only')

  if noworkon:
    cmd.append('--noworkon')

  if noretry:
    cmd.append('--nobuildretry')

  if not chroot_args:
    chroot_args = []

  if chrome_root:
    chroot_args.append('--chrome_root=%s' % chrome_root)

  cmd.extend(packages)
  RunBuildScript(buildroot, cmd, extra_env=extra_env, chroot_args=chroot_args,
                 enter_chroot=True)


FirmwareVersions = collections.namedtuple(
    'FirmwareVersions',
    ['main', 'ec']
)


def GetFirmwareVersions(buildroot, board):
  """Extract version information from the firmware updater, if one exists.

  Args:
    buildroot: The buildroot of the current build.
    board: The board the firmware is for.

  Returns:
    (main fw version, ec fw version)
    Each element will either be set to the string output by the firmware
    updater shellball, or None if there is no firmware updater.
  """
  updater = os.path.join(buildroot, constants.DEFAULT_CHROOT_DIR,
                         cros_build_lib.GetSysroot(board).lstrip(os.path.sep),
                         'usr', 'sbin', 'chromeos-firmwareupdate')
  if not os.path.isfile(updater):
    return FirmwareVersions(None, None)
  updater = path_util.ToChrootPath(updater)

  result = cros_build_lib.RunCommand([updater, '-V'], enter_chroot=True,
                                     capture_output=True, log_output=True,
                                     cwd=buildroot)
  main = re.search(r'BIOS version:\s*(?P<version>.*)', result.output)
  ec = re.search(r'EC version:\s*(?P<version>.*)', result.output)
  return (main.group('version') if main else None,
          ec.group('version') if ec else None)


def BuildImage(buildroot, board, images_to_build, version=None,
               builder_path=None, rootfs_verification=True, extra_env=None,
               disk_layout=None):
  """Run the script which builds images.

  Args:
    buildroot: The buildroot of the current build.
    board: The board of the image.
    images_to_build: The images to be built.
    version: The version of image.
    builder_path: The path of the builder to build the image.
    rootfs_verification: Whether to enable the rootfs verification.
    extra_env: A dictionary of environmental variables to set during generation.
    disk_layout: The disk layout.
  """

  # Default to base if images_to_build is passed empty.
  if not images_to_build:
    images_to_build = ['base']

  version_str = '--version=%s' % (version or '')

  builder_path_str = '--builder_path=%s' % (builder_path or '')

  cmd = ['./build_image', '--board=%s' % board, '--replace', version_str,
         builder_path_str]

  if not rootfs_verification:
    cmd += ['--noenable_rootfs_verification']

  if disk_layout:
    cmd += ['--disk_layout=%s' % disk_layout]

  cmd += images_to_build

  RunBuildScript(buildroot, cmd, extra_env=extra_env, enter_chroot=True)


def GenerateAuZip(buildroot, image_dir, extra_env=None):
  """Run the script which generates au-generator.zip.

  Args:
    buildroot: The buildroot of the current build.
    image_dir: The directory in which to store au-generator.zip.
    extra_env: A dictionary of environmental variables to set during generation.

  Raises:
    failures_lib.BuildScriptFailure if the called script fails.
  """
  chroot_image_dir = path_util.ToChrootPath(image_dir)
  cmd = ['./build_library/generate_au_zip.py', '-o', chroot_image_dir]
  RunBuildScript(buildroot, cmd, extra_env=extra_env, enter_chroot=True)


def TestAuZip(buildroot, image_dir, extra_env=None):
  """Run the script which validates an au-generator.zip.

  Args:
    buildroot: The buildroot of the current build.
    image_dir: The directory in which to find au-generator.zip.
    extra_env: A dictionary of environmental variables to set during generation.

  Raises:
    failures_lib.BuildScriptFailure if the test script fails.
  """
  cmd = ['./build_library/test_au_zip.py', '-o', image_dir]
  RunBuildScript(buildroot, cmd, cwd=constants.CROSUTILS_DIR,
                 extra_env=extra_env)


def BuildVMImageForTesting(buildroot, board, extra_env=None):
  cmd = ['./image_to_vm.sh', '--board=%s' % board, '--test_image']
  RunBuildScript(buildroot, cmd, extra_env=extra_env, enter_chroot=True)


def RunTestImage(buildroot, board, image_dir, results_dir):
  """Executes test_image on the produced image in |image_dir|.

  The "test_image" script will be run as root in chroot. Running the script as
  root will allow the tests to read normally-forbidden files such as those
  owned by root. Running tests inside the chroot allows us to control
  dependencies better.

  Args:
    buildroot: The buildroot of the current build.
    board: The board the image was built for.
    image_dir: The directory in which to find the image.
    results_dir: The directory to store result files.

  Raises:
    failures_lib.BuildScriptFailure if the test script fails.
  """
  cmd = [
      'test_image',
      '--board', board,
      '--test_results_root', path_util.ToChrootPath(results_dir),
      path_util.ToChrootPath(image_dir),
  ]
  RunBuildScript(buildroot, cmd, enter_chroot=True, chromite_cmd=True,
                 sudo=True)


def RunSignerTests(buildroot, board):
  cmd = ['./security_test_image', '--board=%s' % board]
  RunBuildScript(buildroot, cmd, enter_chroot=True)


def RunUnitTests(buildroot, board, blacklist=None, extra_env=None):
  cmd = ['cros_run_unit_tests', '--board=%s' % board]

  if blacklist:
    cmd += ['--blacklist_packages=%s' % ' '.join(blacklist)]

  RunBuildScript(buildroot, cmd, chromite_cmd=True, enter_chroot=True,
                 extra_env=extra_env or {})


def RunTestSuite(buildroot, board, image_path, results_dir, test_type,
                 whitelist_chrome_crashes, archive_dir, ssh_private_key=None):
  """Runs the test harness suite."""
  results_dir_in_chroot = os.path.join(buildroot, 'chroot',
                                       results_dir.lstrip('/'))
  osutils.RmDir(results_dir_in_chroot, ignore_missing=True)

  cwd = os.path.join(buildroot, 'src', 'scripts')
  dut_type = 'gce' if test_type == constants.GCE_VM_TEST_TYPE else 'vm'

  cmd = ['bin/ctest',
         '--board=%s' % board,
         '--type=%s' % dut_type,
         '--no_graphics',
         '--verbose',
         '--target_image=%s' % image_path,
         '--test_results_root=%s' % results_dir_in_chroot
        ]

  if test_type not in constants.VALID_VM_TEST_TYPES:
    raise AssertionError('Unrecognized test type %r' % test_type)

  if test_type == constants.FULL_AU_TEST_TYPE:
    cmd.append('--archive_dir=%s' % archive_dir)
  else:
    if test_type == constants.SMOKE_SUITE_TEST_TYPE:
      cmd.append('--only_verify')
      cmd.append('--suite=smoke')
    elif test_type == constants.GCE_VM_TEST_TYPE:
      cmd.append('--only_verify')
      cmd.append('--suite=gce-smoke')
    elif test_type == constants.TELEMETRY_SUITE_TEST_TYPE:
      cmd.append('--only_verify')
      cmd.append('--suite=telemetry_unit')
    else:
      cmd.append('--quick_update')

  if whitelist_chrome_crashes:
    cmd.append('--whitelist_chrome_crashes')

  if ssh_private_key is not None:
    cmd.append('--ssh_private_key=%s' % ssh_private_key)

  # Give tests 10 minutes to clean up before shutting down.
  result = cros_build_lib.RunCommand(cmd, cwd=cwd, error_code_ok=True,
                                     kill_timeout=10 * 60)
  if result.returncode:
    if os.path.exists(results_dir_in_chroot):
      error = '%s exited with code %d' % (' '.join(cmd), result.returncode)
      with open(results_dir_in_chroot + '/failed_test_command', 'w') as failed:
        failed.write(error)

    raise failures_lib.TestFailure(
        '** VMTests failed with code %d **' % result.returncode)


def RunDevModeTest(buildroot, board, image_dir):
  """Runs the dev mode testing script to verify dev-mode scripts work."""
  crostestutils = os.path.join(buildroot, 'src', 'platform', 'crostestutils')
  image_path = os.path.join(image_dir, constants.TEST_IMAGE_BIN)
  test_script = 'devmode-test/devinstall_test.py'
  cmd = [os.path.join(crostestutils, test_script), '--verbose', board,
         image_path]
  cros_build_lib.RunCommand(cmd)


def RunCrosVMTest(board, image_dir):
  """Runs cros_vm_test script to verify cros commands work."""
  image_path = os.path.join(image_dir, constants.TEST_IMAGE_BIN)
  test = cros_vm_test.CrosVMTest(board, image_path)
  test.Run()


def ListFailedTests(results_path):
  """Returns a list of failed tests.

  Parse the test report logs from autotest to find failed tests.

  Args:
    results_path: Path to the directory of test results.

  Returns:
    A lists of (test_name, relative/path/to/failed/tests)
  """
  # TODO: we don't have to parse the log to find failed tests once
  # crbug.com/350520 is fixed.
  reports = []
  for path, _, filenames in os.walk(results_path):
    reports.extend([os.path.join(path, x) for x in filenames
                    if x == _TEST_REPORT_FILENAME])

  failed_tests = []
  processed_tests = []
  for report in reports:
    logging.info('Parsing test report %s', report)
    # Format used in the report:
    #   /path/to/base/dir/test_harness/all/SimpleTestUpdateAndVerify/ \
    #     2_autotest_tests/results-01-security_OpenSSLBlacklist [  FAILED  ]
    #   /path/to/base/dir/test_harness/all/SimpleTestUpdateAndVerify/ \
    #     2_autotest_tests/results-01-security_OpenSSLBlacklist/ \
    #     security_OpenBlacklist [  FAILED  ]
    with open(report) as f:
      failed_re = re.compile(r'([\./\w-]*)\s*\[\s*(\S+?)\s*\]')
      test_name_re = re.compile(r'results-[\d]+?-([\.\w_]*)')
      for line in f:
        r = failed_re.search(line)
        if r and r.group(2) == _TEST_FAILED:
          # Process only failed tests.
          file_path = r.group(1)
          match = test_name_re.search(file_path)
          if match:
            test_name = match.group(1)
          else:
            # If no match is found (due to format change or other
            # reasons), simply use the last component of file_path.
            test_name = os.path.basename(file_path)

          # A test may have subtests. We don't want to list all subtests.
          if test_name not in processed_tests:
            base_dirname = os.path.basename(results_path)
            # Get the relative path from the test_results directory. Note
            # that file_path is a chroot path, while results_path is a
            # non-chroot path, so we cannot use os.path.relpath directly.
            rel_path = file_path.split(base_dirname)[1].lstrip(os.path.sep)
            failed_tests.append((test_name, rel_path))
            processed_tests.append(test_name)

  return failed_tests


def GetTestResultsDir(buildroot, test_results_dir):
  """Returns the test results directory located in chroot.

  Args:
    buildroot: Root directory where build occurs.
    test_results_dir: Path from buildroot/chroot to find test results.
      This must a subdir of /tmp.
  """
  test_results_dir = test_results_dir.lstrip('/')
  return os.path.join(buildroot, constants.DEFAULT_CHROOT_DIR, test_results_dir)


def ArchiveTestResults(results_path, archive_dir):
  """Archives the test results to |archive_dir|.

  Args:
    results_path: Path to test results.
    archive_dir: Local directory to archive to.
  """
  cros_build_lib.SudoRunCommand(['chmod', '-R', 'a+rw', results_path],
                                print_cmd=False)
  if os.path.exists(archive_dir):
    osutils.RmDir(archive_dir)

  def _ShouldIgnore(dirname, file_list):
    # Note: We exclude VM disk and memory images. Instead, they are
    # archived via ArchiveVMFiles. Also skip any symlinks. gsutil
    # hangs on broken symlinks.
    return [x for x in file_list if
            x.startswith(constants.VM_DISK_PREFIX) or
            x.startswith(constants.VM_MEM_PREFIX) or
            os.path.islink(os.path.join(dirname, x))]

  shutil.copytree(results_path, archive_dir, symlinks=False,
                  ignore=_ShouldIgnore)


def BuildAndArchiveTestResultsTarball(src_dir, buildroot):
  """Create a compressed tarball of test results.

  Args:
    src_dir: The directory containing the test results.
    buildroot: Build root directory.

  Returns:
    The name of the tarball.
  """
  target = '%s.tgz' % src_dir.rstrip(os.path.sep)
  chroot = os.path.join(buildroot, constants.DEFAULT_CHROOT_DIR)
  cros_build_lib.CreateTarball(
      target, src_dir, compression=cros_build_lib.COMP_GZIP,
      chroot=chroot)
  return os.path.basename(target)


def ArchiveVMFiles(buildroot, test_results_dir, archive_path):
  """Archives the VM memory and disk images into tarballs.

  There may be multiple tests (e.g. SimpleTestUpdate and
  SimpleTestUpdateAndVerify), and multiple files for each test (one
  for the VM disk, and one for the VM memory). We create a separate
  tar file for each of these files, so that each can be downloaded
  independently.

  Args:
    buildroot: Build root directory.
    test_results_dir: Path from buildroot/chroot to find test results.
      This must a subdir of /tmp.
    archive_path: Directory the tarballs should be written to.

  Returns:
    The paths to the tarballs.
  """
  images_dir = os.path.join(buildroot, 'chroot', test_results_dir.lstrip('/'))
  images = []
  for path, _, filenames in os.walk(images_dir):
    images.extend([os.path.join(path, filename) for filename in
                   fnmatch.filter(filenames, constants.VM_DISK_PREFIX + '*')])
    images.extend([os.path.join(path, filename) for filename in
                   fnmatch.filter(filenames, constants.VM_MEM_PREFIX + '*')])

  tar_files = []
  for image_path in images:
    image_rel_path = os.path.relpath(image_path, images_dir)
    image_parent_dir = os.path.dirname(image_path)
    image_file = os.path.basename(image_path)
    tarball_path = os.path.join(archive_path,
                                "%s.tar" % image_rel_path.replace('/', '_'))
    # Note that tar will chdir to |image_parent_dir|, so that |image_file|
    # is at the top-level of the tar file.
    cros_build_lib.CreateTarball(tarball_path,
                                 image_parent_dir,
                                 compression=cros_build_lib.COMP_BZIP2,
                                 inputs=[image_file])
    tar_files.append(tarball_path)
  return tar_files


HWTestSuiteResult = collections.namedtuple('HWTestSuiteResult',
                                           ['to_raise', 'json_dump_result'])

@failures_lib.SetFailureType(failures_lib.SuiteTimedOut,
                             timeout_util.TimeoutError)
def RunHWTestSuite(build, suite, board, pool=None, num=None, file_bugs=None,
                   wait_for_results=None, priority=None, timeout_mins=None,
                   retry=None, max_retries=None,
                   minimum_duts=0, suite_min_duts=0,
                   offload_failures_only=None, debug=True, subsystems=None,
                   skip_duts_check=False):
  """Run the test suite in the Autotest lab.

  Args:
    build: The build is described as the bot_id and the build version.
      e.g. x86-mario-release/R18-1655.0.0-a1-b1584.
    suite: Name of the Autotest suite.
    board: The board the test suite should be scheduled against.
    pool: The pool of machines we should use to run the hw tests on.
    num: Maximum number of devices to use when scheduling tests in the
         hardware test lab.
    file_bugs: File bugs on test failures for this suite run.
    wait_for_results: If True, wait for autotest results before returning.
    priority: Priority of this suite run.
    timeout_mins: Timeout in minutes for the suite job and its sub-jobs.
    retry: If True, will enable job-level retry. Only works when
           wait_for_results is True.
    max_retries: Integer, maximum job retries allowed at suite level.
                 None for no max.
    minimum_duts: The minimum number of DUTs should be available in lab for the
                  suite job to be created. If it's set to 0, the check will be
                  skipped.
    suite_min_duts: Preferred minimum duts, lab will prioritize on getting
                    such many duts even if the suite is competing with
                    a suite that has higher priority.
    offload_failures_only: Only offload failed tests to Google Storage.
    debug: Whether we are in debug mode.
    subsystems: A set of subsystems that the relevant changes affect, for
                testing purposes.
    skip_duts_check: If True, skip minimum available DUTs check.

  Returns:
    An instance of named tuple HWTestSuiteResult, the first element is the
    exception to be raised; the second element is the json dump cmd result,
    if json_dump cmd is not called, None will be returned.
  """
  try:
    cmd = [_RUN_SUITE_PATH]
    cmd += _GetRunSuiteArgs(build, suite, board, pool, num, file_bugs,
                            priority, timeout_mins, retry, max_retries,
                            minimum_duts, suite_min_duts, offload_failures_only,
                            subsystems, skip_duts_check)
    swarming_args = _CreateSwarmingArgs(build, suite, board, priority,
                                        timeout_mins)
    running_json_dump_flag = False
    json_dump_result = None
    job_id = _HWTestCreate(cmd, debug, **swarming_args)
    if wait_for_results and job_id:
      _HWTestWait(cmd, job_id, **swarming_args)
      running_json_dump_flag = True
      json_dump_result = _HWTestDumpJson(cmd, job_id, **swarming_args)
    return HWTestSuiteResult(None, json_dump_result)
  except cros_build_lib.RunCommandError as e:
    result = e.result
    if not result.HasValidSummary():
      # swarming client has failed.
      logging.error('No valid task summary json generated, output:%s',
                    result.output)
      if result.task_summary_json:
        logging.error('Invalid task summary json:\n%s',
                      json.dumps(result.task_summary_json, indent=2))
      to_raise = failures_lib.SwarmingProxyFailure(
          '** Failed to fullfill request with proxy server, code(%d) **'
          % result.returncode)
    elif result.GetValue('internal_failure'):
      logging.error('Encountered swarming internal error:\n'
                    'stdout: \n%s\n'
                    'summary json content:\n%s',
                    result.output, str(result.task_summary_json))
      to_raise = failures_lib.SwarmingProxyFailure(
          '** Failed to fullfill request with proxy server, code(%d) **'
          % result.returncode)
    else:
      logging.debug('swarming info: name: %s, bot_id: %s, task_id: %s, '
                    'created_ts: %s',
                    result.GetValue('name'),
                    result.GetValue('bot_id'),
                    result.GetValue('id'),
                    result.GetValue('created_ts'))
      # If running json_dump cmd, write the pass/fail subsys dict into console,
      # otherwise, write the cmd output to the console.
      outputs = result.GetValue('outputs', '')
      if running_json_dump_flag:
        s = ''.join(outputs)
        sys.stdout.write(s)
        sys.stdout.write('\n')
        i = s.find(JSON_DICT_START) + len(JSON_DICT_START)
        j = s.find(JSON_DICT_END)
        json_dump_result = json.loads(s[i:j])
      else:
        for output in outputs:
          sys.stdout.write(output)
        sys.stdout.flush()
      # swarming client has submitted task and returned task information.
      lab_warning_codes = (2,)
      infra_error_codes = (3,)
      timeout_codes = (4,)
      board_not_available_codes = (5,)
      proxy_failure_codes = (241,)

      if result.returncode in lab_warning_codes:
        to_raise = failures_lib.TestWarning(
            '** Suite passed with a warning code **')
      elif (result.returncode in infra_error_codes or
            result.returncode in proxy_failure_codes):
        to_raise = failures_lib.TestLabFailure(
            '** HWTest did not complete due to infrastructure issues '
            '(code %d) **' % result.returncode)
      elif result.returncode in timeout_codes:
        to_raise = failures_lib.SuiteTimedOut(
            '** Suite timed out before completion **')
      elif result.returncode in board_not_available_codes:
        to_raise = failures_lib.BoardNotAvailable(
            '** Board was not availble in the lab **')
      elif result.returncode != 0:
        to_raise = failures_lib.TestFailure(
            '** HWTest failed (code %d) **' % result.returncode)
    return HWTestSuiteResult(to_raise, json_dump_result)


# pylint: disable=docstring-missing-args
def _GetRunSuiteArgs(build, suite, board, pool=None, num=None,
                     file_bugs=None, priority=None, timeout_mins=None,
                     retry=None, max_retries=None, minimum_duts=0,
                     suite_min_duts=0, offload_failures_only=None,
                     subsystems=None, skip_duts_check=False):
  """Get a list of args for run_suite.

  Args:
    See RunHWTestSuite.

  Returns:
    A list of args for run_suite
  """
  args = ['--build', build, '--board', board]

  if subsystems:
    args += ['--suite_name', 'suite_attr_wrapper']
  else:
    args += ['--suite_name', suite]

  # Add optional arguments to command, if present.
  if pool is not None:
    args += ['--pool', pool]

  if num is not None:
    args += ['--num', str(num)]

  if file_bugs is not None:
    args += ['--file_bugs', str(file_bugs)]

  if priority is not None:
    args += ['--priority', priority]

  if timeout_mins is not None:
    args += ['--timeout_mins', str(timeout_mins)]

  if retry is not None:
    args += ['--retry', str(retry)]

  if max_retries is not None:
    args += ['--max_retries', str(max_retries)]

  if minimum_duts != 0:
    args += ['--minimum_duts', str(minimum_duts)]

  if suite_min_duts != 0:
    args += ['--suite_min_duts', str(suite_min_duts)]

  if offload_failures_only is not None:
    args += ['--offload_failures_only', str(offload_failures_only)]

  if subsystems:
    subsystem_attr = ['subsystem:%s' % x for x in subsystems]
    subsystems_attr_str = ' or '.join(subsystem_attr)

    if suite != 'suite_attr_wrapper':
      if type(suite) is str:
        suite_attr_str = 'suite:%s' % suite
      else:
        suite_attr_str = ' or '.join(['suite:%s' % x for x in suite])

      attr_value = '(%s) and (%s)' % (suite_attr_str, subsystems_attr_str)
    else:
      attr_value = subsystems_attr_str

    suite_args_dict = repr({'attr_filter' : attr_value})
    args += ['--suite_args', suite_args_dict]

    if skip_duts_check:
      args += ['--skip_duts_check']
  return args


def _CreateSwarmingArgs(build, suite, board, priority, timeout_mins=None):
  """Create args for swarming client.

  Args:
    build: Name of the build, will be part of the swarming task name.
    suite: Name of the suite, will be part of the swarming task name.
    timeout_mins: run_suite timeout mins, will be used to figure out
                  timeouts for swarming task.
    board: Name of the board.
    priority: Priority of this call.

  Returns:
    A dictionary of args for swarming client.
  """

  swarming_timeout = timeout_mins or _DEFAULT_HWTEST_TIMEOUT_MINS
  swarming_timeout = swarming_timeout * 60 + _SWARMING_ADDITIONAL_TIMEOUT

  return {
      'swarming_server': topology.topology.get(
          topology.SWARMING_PROXY_HOST_KEY),
      'task_name': '-'.join([build, suite]),
      'dimensions': [('os', 'Ubuntu-14.04'),
                     ('pool', 'default')],
      'print_status_updates': True,
      'timeout_secs': swarming_timeout,
      'io_timeout_secs': swarming_timeout,
      'hard_timeout_secs': swarming_timeout,
      'expiration_secs': _SWARMING_EXPIRATION,
      'tags': {
          'task_name': '-'.join([build, suite]),
          'build': build,
          'suite': suite,
          'board': board,
          'priority': priority,
      },
  }


def _HWTestCreate(cmd, debug=False, **kwargs):
  """Start a suite in the HWTest lab, and return its id.

  This method runs a command to create the suite. Since we are using
  swarming client, which contiuously send request to swarming server
  to poll task result, there is no need to retry on any network
  related failures.

  Args:
    cmd: Proxied run_suite command.
    debug: If True, log command rather than running it.
    kwargs: args to be passed to RunSwarmingCommand.

  Returns:
    Job id of created suite. Returned id will be None if no job id was created.
  """
  # Start the suite.
  start_cmd = list(cmd) + ['-c']

  if debug:
    logging.info('RunHWTestSuite would run: %s',
                 cros_build_lib.CmdToStr(start_cmd))
  else:
    result = swarming_lib.RunSwarmingCommandWithRetries(
        max_retry=_MAX_HWTEST_START_CMD_RETRY,
        error_check=swarming_lib.SwarmingRetriableErrorCheck,
        cmd=start_cmd, capture_output=True, combine_stdout_stderr=True,
        **kwargs)
    # If the command succeeds, result.task_summary_json
    # should have the right content.
    for output in result.GetValue('outputs', ''):
      sys.stdout.write(output)
    sys.stdout.flush()
    m = re.search(r'Created suite job:.*object_id=(?P<job_id>\d*)',
                  result.output)
    if m:
      return m.group('job_id')
  return None

def _HWTestWait(cmd, job_id, **kwargs):
  """Wait for HWTest suite to complete.

  Args:
    cmd: Proxied run_suite command.
    job_id: The job id of the suite that was created.
    kwargs: args to be passed to RunSwarmingCommand.
  """
  # Wait on the suite
  wait_cmd = list(cmd) + ['-m', str(job_id)]
  try:
    result = swarming_lib.RunSwarmingCommandWithRetries(
        max_retry=_MAX_HWTEST_CMD_RETRY,
        error_check=swarming_lib.SwarmingRetriableErrorCheck,
        cmd=wait_cmd, capture_output=True, combine_stdout_stderr=True,
        **kwargs)
  except cros_build_lib.RunCommandError as e:
    result = e.result
    # Delay the lab-related exceptions, since those will be raised in the next
    # json_dump cmd run.
    if (not result.task_summary_json or not result.GetValue('outputs') or
        result.GetValue('internal_failure')):
      raise
    else:
      logging.error('wait_cmd has lab failures: %s.\nException will be raised '
                    'in the next json_dump run.', e.msg)
  for output in result.GetValue('outputs', ''):
    sys.stdout.write(output)
  sys.stdout.flush()


def _HWTestDumpJson(cmd, job_id, **kwargs):
  """Consume HWTest suite json output and return passed/failed subsystems dict.

  Args:
    cmd: Proxied run_suite command.
    job_id: The job id of the suite that was created.
    kwargs: args to be passed to RunSwarmingCommand.

  Returns:
    The parsed json_dump dictionary.
  """
  dump_json_cmd = list(cmd) + ['--json_dump', '-m', str(job_id)]
  result = swarming_lib.RunSwarmingCommandWithRetries(
      max_retry=_MAX_HWTEST_CMD_RETRY,
      error_check=swarming_lib.SwarmingRetriableErrorCheck,
      cmd=dump_json_cmd, capture_output=True, combine_stdout_stderr=True,
      **kwargs)
  for output in result.GetValue('outputs', ''):
    sys.stdout.write(output)
  sys.stdout.write('\n')
  sys.stdout.flush()
  dump_output = ''.join(result.GetValue('outputs', ''))
  i = dump_output.find(JSON_DICT_START) + len(JSON_DICT_START)
  j = dump_output.find(JSON_DICT_END)
  if i == -1 or j == -1 or i > j:
    raise ValueError('Invalid swarming output: %s' % dump_output)
  return json.loads(dump_output[i:j])


def AbortHWTests(config_type_or_name, version, debug, suite=''):
  """Abort the specified hardware tests for the given bot(s).

  Args:
    config_type_or_name: Either the name of the builder (e.g. link-paladin) or
                         the config type if you want to abort all HWTests for
                         that config (e.g. config_lib.CONFIG_TYPE_FULL).
    version: The version of the current build. E.g. R18-1655.0.0-rc1
    debug: Whether we are in debug mode.
    suite: Name of the Autotest suite. If empty, abort all suites.
  """
  # Abort all jobs for the given config and version.
  # Example for a specific config: link-paladin/R35-5542.0.0-rc1
  # Example for a config type: paladin/R35-5542.0.0-rc1
  substr = '%s/%s' % (config_type_or_name, version)
  abort_args = ['-i', substr, '-s', suite]
  try:
    cmd = [_ABORT_SUITE_PATH] + abort_args
    swarming_args = {
        'swarming_server': topology.topology.get(
            topology.SWARMING_PROXY_HOST_KEY),
        'task_name': '-'.join(['abort', substr, suite]),
        'dimensions': [('os', 'Ubuntu-14.04'),
                       ('pool', 'default')],
        'print_status_updates': True,
        'expiration_secs': _SWARMING_EXPIRATION}
    if debug:
      logging.info('AbortHWTests would run the cmd via '
                   'swarming, cmd: %s, swarming_args: %s',
                   cros_build_lib.CmdToStr(cmd), str(swarming_args))
    else:
      swarming_lib.RunSwarmingCommand(cmd, **swarming_args)
  except cros_build_lib.RunCommandError:
    logging.warning('AbortHWTests failed', exc_info=True)


def GenerateStackTraces(buildroot, board, test_results_dir,
                        archive_dir, got_symbols):
  """Generates stack traces for logs in |gzipped_test_tarball|

  Args:
    buildroot: Root directory where build occurs.
    board: Name of the board being worked on.
    test_results_dir: Directory of the test results.
    archive_dir: Local directory for archiving.
    got_symbols: True if breakpad symbols have been generated.

  Returns:
    List of stack trace file names.
  """
  stack_trace_filenames = []
  asan_log_signaled = False

  board_path = cros_build_lib.GetSysroot(board=board)
  symbol_dir = os.path.join(board_path, 'usr', 'lib', 'debug', 'breakpad')
  for curr_dir, _subdirs, files in os.walk(test_results_dir):
    for curr_file in files:
      full_file_path = os.path.join(curr_dir, curr_file)
      processed_file_path = '%s.txt' % full_file_path

      # Distinguish whether the current file is a minidump or asan_log.
      if curr_file.endswith('.dmp'):
        # Skip crash files that were purposely generated or if
        # breakpad symbols are absent.
        if not got_symbols or curr_file.find('crasher_nobreakpad') == 0:
          continue
        # Precess the minidump from within chroot.
        minidump = path_util.ToChrootPath(full_file_path)
        cwd = os.path.join(buildroot, 'src', 'scripts')
        cros_build_lib.RunCommand(
            ['minidump_stackwalk', minidump, symbol_dir], cwd=cwd,
            enter_chroot=True, error_code_ok=True, redirect_stderr=True,
            debug_level=logging.DEBUG, log_stdout_to_file=processed_file_path)
      # Process asan log.
      else:
        # Prepend '/chrome/$board' path to the stack trace in log.
        log_content = ''
        with open(full_file_path) as f:
          for line in f:
            # Stack frame line example to be matched here:
            #    #0 0x721d1831 (/opt/google/chrome/chrome+0xb837831)
            stackline_match = re.search(r'^ *#[0-9]* 0x.* \(', line)
            if stackline_match:
              frame_end = stackline_match.span()[1]
              line = line[:frame_end] + board_path + line[frame_end:]
            log_content += line
        # Symbolize and demangle it.
        raw = cros_build_lib.RunCommand(
            ['asan_symbolize.py'], input=log_content, enter_chroot=True,
            debug_level=logging.DEBUG, capture_output=True,
            extra_env={'LLVM_SYMBOLIZER_PATH' : '/usr/bin/llvm-symbolizer'})
        cros_build_lib.RunCommand(['c++filt'],
                                  input=raw.output, debug_level=logging.DEBUG,
                                  cwd=buildroot, redirect_stderr=True,
                                  log_stdout_to_file=processed_file_path)
        # Break the bot if asan_log found. This is because some asan
        # crashes may not fail any test so the bot stays green.
        # Ex: crbug.com/167497
        if not asan_log_signaled:
          asan_log_signaled = True
          logging.error('Asan crash occurred. See asan_logs in Artifacts.')
          logging.PrintBuildbotStepFailure()

      # Append the processed file to archive.
      filename = ArchiveFile(processed_file_path, archive_dir)
      stack_trace_filenames.append(filename)

  return stack_trace_filenames


@failures_lib.SetFailureType(failures_lib.BuilderFailure)
def ArchiveFile(file_to_archive, archive_dir):
  """Archives the specified file.

  Args:
    file_to_archive: Full path to file to archive.
    archive_dir: Local directory for archiving.

  Returns:
    The base name of the archived file.
  """
  filename = os.path.basename(file_to_archive)
  if archive_dir:
    archived_file = os.path.join(archive_dir, filename)
    shutil.copy(file_to_archive, archived_file)
    os.chmod(archived_file, 0o644)

  return filename


class AndroidIsPinnedUprevError(failures_lib.InfrastructureFailure):
  """Raised when we try to uprev while Android is pinned."""

  def __init__(self, new_android_atom):
    """Initialize a AndroidIsPinnedUprevError.

    Args:
      new_android_atom: The Android atom that we failed to
                        uprev to, due to Android being pinned.
    """
    msg = ('Failed up uprev to Android version %s as Android was pinned.' %
           new_android_atom)
    super(AndroidIsPinnedUprevError, self).__init__(msg)
    self.new_android_atom = new_android_atom


class ChromeIsPinnedUprevError(failures_lib.InfrastructureFailure):
  """Raised when we try to uprev while chrome is pinned."""

  def __init__(self, new_chrome_atom):
    """Initialize a ChromeIsPinnedUprevError.

    Args:
      new_chrome_atom: The chrome atom that we failed to
                       uprev to, due to chrome being pinned.
    """
    msg = ('Failed up uprev to chrome version %s as chrome was pinned.' %
           new_chrome_atom)
    super(ChromeIsPinnedUprevError, self).__init__(msg)
    self.new_chrome_atom = new_chrome_atom


def MarkAndroidAsStable(buildroot, tracking_branch, boards,
                        android_version=None):
  """Returns the portage atom for the revved Android ebuild - see man emerge."""
  # TODO: Consider merging this with MarkChromeAsStable.
  command = ['cros_mark_android_as_stable',
             '--tracking_branch=%s' % tracking_branch]
  if boards:
    command.append('--boards=%s' % ':'.join(boards))
  if android_version:
    command.append('--force_version=%s' % android_version)

  portage_atom_string = RunBuildScript(buildroot, command, chromite_cmd=True,
                                       enter_chroot=True,
                                       redirect_stdout=True).output.rstrip()
  android_atom = None
  if portage_atom_string:
    android_atom = portage_atom_string.splitlines()[-1].partition('=')[-1]
  if not android_atom:
    logging.info('Found nothing to rev.')
    return None

  for board in boards:
    # Sanity check: We should always be able to merge the version of
    # Android we just unmasked.
    try:
      command = ['emerge-%s' % board, '-p', '--quiet', '=%s' % android_atom]
      RunBuildScript(buildroot, command, enter_chroot=True,
                     combine_stdout_stderr=True, capture_output=True)
    except failures_lib.BuildScriptFailure:
      logging.error('Cannot emerge-%s =%s\nIs Android pinned to an older '
                    'version?' % (board, android_atom))
      raise AndroidIsPinnedUprevError(android_atom)

  return android_atom


def MarkChromeAsStable(buildroot,
                       tracking_branch,
                       chrome_rev,
                       boards,
                       chrome_version=None):
  """Returns the portage atom for the revved chrome ebuild - see man emerge."""
  cwd = os.path.join(buildroot, 'src', 'scripts')
  extra_env = None
  chroot_args = None

  command = ['../../chromite/bin/cros_mark_chrome_as_stable',
             '--tracking_branch=%s' % tracking_branch]
  if boards:
    command.append('--boards=%s' % ':'.join(boards))
  if chrome_version:
    command.append('--force_version=%s' % chrome_version)

  portage_atom_string = cros_build_lib.RunCommand(
      command + [chrome_rev],
      cwd=cwd,
      redirect_stdout=True,
      enter_chroot=True,
      chroot_args=chroot_args,
      extra_env=extra_env).output.rstrip()
  chrome_atom = None
  if portage_atom_string:
    chrome_atom = portage_atom_string.splitlines()[-1].partition('=')[-1]
  if not chrome_atom:
    logging.info('Found nothing to rev.')
    return None

  for board in boards:
    # If we're using a version of Chrome other than the latest one, we need
    # to unmask it manually.
    if chrome_rev != constants.CHROME_REV_LATEST:
      keywords_file = CHROME_KEYWORDS_FILE % {'board': board}
      for keywords_file in (CHROME_KEYWORDS_FILE % {'board': board},
                            CHROME_UNMASK_FILE % {'board': board}):
        cros_build_lib.SudoRunCommand(
            ['mkdir', '-p', os.path.dirname(keywords_file)],
            enter_chroot=True, cwd=cwd)
        cros_build_lib.SudoRunCommand(
            ['tee', keywords_file], input='=%s\n' % chrome_atom,
            enter_chroot=True, cwd=cwd)

    # Sanity check: We should always be able to merge the version of
    # Chrome we just unmasked.
    try:
      cros_build_lib.RunCommand(
          ['emerge-%s' % board, '-p', '--quiet', '=%s' % chrome_atom],
          enter_chroot=True, combine_stdout_stderr=True, capture_output=True)
    except cros_build_lib.RunCommandError:
      logging.error('Cannot emerge-%s =%s\nIs Chrome pinned to an older '
                    'version?' % (board, chrome_atom))
      raise ChromeIsPinnedUprevError(chrome_atom)

  return chrome_atom


def CleanupChromeKeywordsFile(boards, buildroot):
  """Cleans chrome uprev artifact if it exists."""
  for board in boards:
    keywords_path_in_chroot = CHROME_KEYWORDS_FILE % {'board': board}
    keywords_file = '%s/chroot%s' % (buildroot, keywords_path_in_chroot)
    if os.path.exists(keywords_file):
      cros_build_lib.SudoRunCommand(['rm', '-f', keywords_file])


def UprevPackages(buildroot, boards, overlays):
  """Uprevs non-browser chromium os packages that have changed."""
  drop_file = _PACKAGE_FILE % {'buildroot': buildroot}
  cmd = ['cros_mark_as_stable', '--all',
         '--boards=%s' % ':'.join(boards),
         '--overlays=%s' % ':'.join(overlays),
         '--drop_file=%s' % drop_file,
         'commit']
  RunBuildScript(buildroot, cmd, chromite_cmd=True)


def UprevPush(buildroot, overlays, dryrun, staging_branch=None):
  """Pushes uprev changes to the main line.

  Args:
    buildroot: Root directory where build occurs.
    overlays: The overlays to push uprevs.
    dryrun: If True, do not actually push.
    staging_branch: If not None, push uprev commits to this
                    staging_branch.
  """
  cmd = ['cros_mark_as_stable',
         '--srcroot=%s' % os.path.join(buildroot, 'src'),
         '--overlays=%s' % ':'.join(overlays)
        ]
  if staging_branch is not None:
    cmd.append('--staging_branch=%s' % staging_branch)
  if dryrun:
    cmd.append('--dryrun')
  cmd.append('push')
  RunBuildScript(buildroot, cmd, chromite_cmd=True)


def ExtractDependencies(buildroot, packages, board=None, useflags=None,
                        cpe_format=False, raw_cmd_result=False):
  """Extracts dependencies for |packages|.

  Args:
    buildroot: The root directory where the build occurs.
    packages: A list of packages for which to extract dependencies.
    board: Board type that was built on this machine.
    useflags: A list of useflags for this build.
    cpe_format: Set output format to CPE-only JSON; otherwise,
      output traditional deps.
    raw_cmd_result: If set True, returns the CommandResult object.
      Otherwise, returns the dependencies as a dictionary.

  Returns:
    Returns the CommandResult object if |raw_cmd_result| is set; returns
    the dependencies in a dictionary otherwise.
  """
  cmd = ['cros_extract_deps']
  if board:
    cmd += ['--board', board]
  if cpe_format:
    cmd += ['--format=cpe']
  else:
    cmd += ['--format=deps']
  cmd += packages
  env = {}
  if useflags:
    env['USE'] = ' '.join(useflags)

  if raw_cmd_result:
    return RunBuildScript(
        buildroot, cmd, enter_chroot=True, chromite_cmd=True,
        capture_output=True, extra_env=env)

  # The stdout of cros_extract_deps may contain undesirable
  # output. Avoid that by instructing the script to explicitly dump
  # the deps into a file.
  with tempfile.NamedTemporaryFile(
      dir=os.path.join(buildroot, 'chroot', 'tmp')) as f:
    cmd += ['--output-path', path_util.ToChrootPath(f.name)]
    RunBuildScript(buildroot, cmd, enter_chroot=True,
                   chromite_cmd=True, capture_output=True, extra_env=env)
    return json.loads(f.read())


def GenerateCPEExport(buildroot, board, useflags=None):
  """Generate CPE export.

  Args:
    buildroot: The root directory where the build occurs.
    board: Board type that was built on this machine.
    useflags: A list of useflags for this build.

  Returns:
    A CommandResult object with the results of running the CPE
    export command.
  """
  return ExtractDependencies(
      buildroot, ['virtual/target-os'], board=board,
      useflags=useflags, cpe_format=True, raw_cmd_result=True)


def GenerateBreakpadSymbols(buildroot, board, debug):
  """Generate breakpad symbols.

  Args:
    buildroot: The root directory where the build occurs.
    board: Board type that was built on this machine.
    debug: Include extra debugging output.
  """
  # We don't care about firmware symbols.
  # See http://crbug.com/213670.
  exclude_dirs = ['firmware']

  cmd = ['cros_generate_breakpad_symbols', '--board=%s' % board,
         '--jobs=%s' % str(max([1, multiprocessing.cpu_count() / 2]))]
  cmd += ['--exclude-dir=%s' % x for x in exclude_dirs]
  if debug:
    cmd += ['--debug']
  RunBuildScript(buildroot, cmd, enter_chroot=True, chromite_cmd=True)


def GenerateDebugTarball(buildroot, board, archive_path, gdb_symbols,
                         archive_name='debug.tgz'):
  """Generates a debug tarball in the archive_dir.

  Args:
    buildroot: The root directory where the build occurs.
    board: Board type that was built on this machine
    archive_path: Directory where tarball should be stored.
    gdb_symbols: Include *.debug files for debugging core files with gdb.
    archive_name: Name of the tarball to generate.

  Returns:
    The filename of the created debug tarball.
  """
  # Generate debug tarball. This needs to run as root because some of the
  # symbols are only readable by root.
  chroot = os.path.join(buildroot, 'chroot')
  board_dir = os.path.join(chroot, 'build', board, 'usr', 'lib')
  debug_tarball = os.path.join(archive_path, archive_name)
  extra_args = None
  inputs = None

  if gdb_symbols:
    extra_args = ['--exclude',
                  os.path.join('debug', constants.AUTOTEST_BUILD_PATH),
                  '--exclude', 'debug/tests']
    inputs = ['debug']
  else:
    inputs = ['debug/breakpad']

  compression = cros_build_lib.CompressionExtToType(debug_tarball)
  cros_build_lib.CreateTarball(
      debug_tarball, board_dir, sudo=True, compression=compression,
      inputs=inputs, extra_args=extra_args)

  # Fix permissions and ownership on debug tarball.
  cros_build_lib.SudoRunCommand(['chown', str(os.getuid()), debug_tarball])
  os.chmod(debug_tarball, 0o644)

  return os.path.basename(debug_tarball)


def GenerateHtmlIndex(index, files, title='Index', url_base=None):
  """Generate a simple index.html file given a set of filenames

  Args:
    index: The file to write the html index to.
    files: The list of files to create the index of.  If a string, then it
           may be a path to a file (with one file per line), or a directory
           (which will be listed).
    title: Title string for the HTML file.
    url_base: The URL to prefix to all elements (otherwise they'll be relative).
  """
  def GenLink(target, name=None):
    if name == '':
      return ''
    return ('<li><a href="%s%s">%s</a></li>'
            % (url_base, target, name if name else target))

  if isinstance(files, (unicode, str)):
    if os.path.isdir(files):
      files = os.listdir(files)
    else:
      files = osutils.ReadFile(files).splitlines()
  url_base = url_base + '/' if url_base else ''

  # Head + open list.
  html = '<html>'
  html += '<head><title>%s</title></head>' % title
  html += '<body><h2>%s</h2><ul>' % title

  # List members.
  dot = ('.',)
  dot_dot = ('..',)
  links = []
  for a in sorted(set(files)):
    a = a.split('|')
    if a[0] == '.':
      dot = a
    elif a[0] == '..':
      dot_dot = a
    else:
      links.append(GenLink(*a))
  links.insert(0, GenLink(*dot_dot))
  links.insert(0, GenLink(*dot))
  html += '\n'.join(links)

  # Close list and file.
  html += '</ul></body></html>'

  osutils.WriteFile(index, html)


def GenerateHtmlTimeline(timeline, rows, title):
  """Generate a simple timeline.html file given a list of timings.

  Args:
    index: The file to write the html index to.
    rows: The list of rows to generate a timeline of.  Each row should be
          tuple of (entry, start_time, end_time)
    title: Title of the timeline.
  """

  _HTML = """<html>
  <head>
    <title>%(title)s</title>
    %(javascript)s
  </head>
  <body>
    <h2>%(title)s</h2>
    <!--Div that will hold the timeline-->
    <div id="chart_div"></div>
  </body>
</html>
"""

  _JAVASCRIPT_HEAD = """
    <!--Load the AJAX API-->
    <script type="text/javascript" src="https://www.gstatic.com/charts/loader.js"></script>
    <script type="text/javascript">

      // Load the Visualization API and the timeline package.
      google.charts.load('current', {'packages':['timeline']});

      // Set a callback to run when the Google Visualization API is loaded.
      google.charts.setOnLoadCallback(drawChart);

      // Callback that creates and populates a data table,
      // instantiates the timeline, passes in the data and
      // draws it.
      function drawChart() {

        // Create the data table.
        var container = document.getElementById('chart_div');
        var data = new google.visualization.DataTable();
        data.addColumn('string', 'Entry');
        data.addColumn('datetime', 'Start Time');
        data.addColumn('datetime', 'Finish Time');
"""

  _JAVASCRIPT_TAIL = """
        // Set chart options
        var height = data.getNumberOfRows() * 50 + 30;
        var options = {'title':'timings',
                       'width':'100%',
                       'height':height};

        // Instantiate and draw our chart, passing in some options.
        var chart = new google.visualization.Timeline(container);
        chart.draw(data, options);
      }
    </script>
"""
  def GenRow(entry, start, end):
    def GenDate(time):
      # Javascript months are 0..11 instead of 1..12
      return ('new Date(%d, %d, %d, %d, %d, %d)' %
              (time.year, time.month - 1, time.day,
               time.hour, time.minute, time.second))
    return ('data.addRow(["%s", %s, %s]);\n' %
            (entry, GenDate(start), GenDate(end)))

  now = datetime.datetime.utcnow()
  rows = [(entry, start, end or now)
          for (entry, start, end) in rows
          if entry and start]

  javascript = [_JAVASCRIPT_HEAD]
  javascript += [GenRow(entry, start, end) for (entry, start, end) in rows]
  javascript.append(_JAVASCRIPT_TAIL)

  data = {
      'javascript': ''.join(javascript),
      'title': title if title else ''
  }

  html = _HTML % data

  osutils.WriteFile(timeline, html)


@failures_lib.SetFailureType(failures_lib.GSUploadFailure)
def _UploadPathToGS(local_path, upload_urls, debug, timeout, acl=None):
  """Upload |local_path| to Google Storage.

  Args:
    local_path: Local path to upload.
    upload_urls: Iterable of GS locations to upload to.
    debug: Whether we are in debug mode.
    filename: Filename of the file to upload.
    timeout: Timeout in seconds.
    acl: Canned gsutil acl to use.
  """
  gs_context = gs.GSContext(acl=acl, dry_run=debug)
  for upload_url in upload_urls:
    with timeout_util.Timeout(timeout):
      gs_context.CopyInto(local_path, upload_url, parallel=True,
                          recursive=True)


@failures_lib.SetFailureType(failures_lib.InfrastructureFailure)
def UploadArchivedFile(archive_dir, upload_urls, filename, debug,
                       update_list=False, timeout=2 * 60 * 60, acl=None):
  """Uploads |filename| in |archive_dir| to Google Storage.

  Args:
    archive_dir: Path to the archive directory.
    upload_urls: Iterable of GS locations to upload to.
    debug: Whether we are in debug mode.
    filename: Name of the file to upload.
    update_list: Flag to update the list of uploaded files.
    timeout: Timeout in seconds.
    acl: Canned gsutil acl to use.
  """
  # Upload the file.
  file_path = os.path.join(archive_dir, filename)
  _UploadPathToGS(file_path, upload_urls, debug, timeout, acl=acl)

  if update_list:
    # Append |filename| to the local list of uploaded files and archive
    # the list to Google Storage. As long as the |filename| string is
    # less than PIPE_BUF (> 512 bytes), the append is atomic.
    uploaded_file_path = os.path.join(archive_dir, UPLOADED_LIST_FILENAME)
    osutils.WriteFile(uploaded_file_path, filename + '\n', mode='a')
    _UploadPathToGS(uploaded_file_path, upload_urls, debug, timeout)


def UploadSymbols(buildroot, board=None, official=False, cnt=None,
                  failed_list=None, breakpad_root=None, product_name=None):
  """Upload debug symbols for this build."""
  cmd = ['upload_symbols', '--yes', '--dedupe']

  if board is not None:
    # Board requires both root and board to be set to be useful.
    cmd += [
        '--root', os.path.join(buildroot, constants.DEFAULT_CHROOT_DIR),
        '--board', board]
  if official:
    cmd.append('--official_build')
  if cnt is not None:
    cmd += ['--upload-limit', str(cnt)]
  if failed_list is not None:
    cmd += ['--failed-list', str(failed_list)]
  if breakpad_root is not None:
    cmd += ['--breakpad_root', breakpad_root]
  if product_name is not None:
    cmd += ['--product_name', product_name]

  # We don't want to import upload_symbols directly because it uses the
  # swarming module which itself imports a _lot_ of stuff.  It has also
  # been known to hang.  We want to keep cbuildbot isolated & robust.
  RunBuildScript(buildroot, cmd, chromite_cmd=True)


def PushImages(board, archive_url, dryrun, profile, sign_types=()):
  """Push the generated image to the release bucket for signing."""
  # Log the equivalent command for debugging purposes.
  log_cmd = ['pushimage', '--board=%s' % board]

  if dryrun:
    log_cmd.append('-n')

  if profile:
    log_cmd.append('--profile=%s' % profile)

  if sign_types:
    log_cmd.append('--sign-types=%s' % ' '.join(sign_types))

  log_cmd.append(archive_url)
  logging.info('Running: %s' % cros_build_lib.CmdToStr(log_cmd))

  try:
    return pushimage.PushImage(archive_url, board, profile=profile,
                               sign_types=sign_types, dry_run=dryrun)
  except pushimage.PushError as e:
    logging.PrintBuildbotStepFailure()
    return e.args[1]


def BuildFactoryInstallImage(buildroot, board, extra_env):
  """Build a factory install image.

  Args:
    buildroot: Root directory where build occurs.
    board: Board type that was built on this machine
    extra_env: Flags to be added to the environment for the new process.

  Returns:
    The basename of the symlink created for the image.
  """

  # We use build_attempt=3 here to ensure that this image uses a different
  # output directory from our regular image and the factory test image.
  alias = _FACTORY_SHIM
  cmd = ['./build_image',
         '--board=%s' % board,
         '--replace',
         '--symlink=%s' % alias,
         '--build_attempt=3',
         'factory_install']
  RunBuildScript(buildroot, cmd, extra_env=extra_env, capture_output=True,
                 enter_chroot=True)
  return alias


def MakeNetboot(buildroot, board, image_dir):
  """Build a netboot image.

  Args:
    buildroot: Root directory where build occurs.
    board: Board type that was built on this machine.
    image_dir: Directory containing factory install shim.
  """
  cmd = ['./make_netboot.sh',
         '--board=%s' % board,
         '--image_dir=%s' % path_util.ToChrootPath(image_dir)]
  RunBuildScript(buildroot, cmd, capture_output=True, enter_chroot=True)


def BuildRecoveryImage(buildroot, board, image_dir, extra_env):
  """Build a recovery image.

  Args:
    buildroot: Root directory where build occurs.
    board: Board type that was built on this machine.
    image_dir: Directory containing base image.
    extra_env: Flags to be added to the environment for the new process.
  """
  base_image = os.path.join(image_dir, constants.BASE_IMAGE_BIN)
  # mod_image_for_recovery leaves behind some artifacts in the source directory
  # that we don't care about. So, use a tempdir as the working directory.
  # This tempdir needs to be at a chroot accessible path.
  with osutils.TempDir(base_dir=image_dir) as tempdir:
    tempdir_base_image = os.path.join(tempdir, constants.BASE_IMAGE_BIN)
    tempdir_recovery_image = os.path.join(tempdir, constants.RECOVERY_IMAGE_BIN)

    # Copy the base image. Symlinking isn't enough because image building
    # scripts follow symlinks by design.
    shutil.copyfile(base_image, tempdir_base_image)
    cmd = ['./mod_image_for_recovery.sh',
           '--board=%s' % board,
           '--image=%s' % path_util.ToChrootPath(tempdir_base_image)]
    RunBuildScript(buildroot, cmd, extra_env=extra_env, capture_output=True,
                   enter_chroot=True)
    shutil.move(tempdir_recovery_image, image_dir)


def BuildTarball(buildroot, input_list, tarball_output, cwd=None,
                 compressed=True, **kwargs):
  """Tars and zips files and directories from input_list to tarball_output.

  Args:
    buildroot: Root directory where build occurs.
    input_list: A list of files and directories to be archived.
    tarball_output: Path of output tar archive file.
    cwd: Current working directory when tar command is executed.
    compressed: Whether or not the tarball should be compressed with pbzip2.
    **kwargs: Keyword arguments to pass to CreateTarball.

  Returns:
    Return value of cros_build_lib.CreateTarball.
  """
  compressor = cros_build_lib.COMP_NONE
  chroot = None
  if compressed:
    compressor = cros_build_lib.COMP_BZIP2
    chroot = os.path.join(buildroot, 'chroot')
  return cros_build_lib.CreateTarball(
      tarball_output, cwd, compression=compressor, chroot=chroot,
      inputs=input_list, **kwargs)


def FindFilesWithPattern(pattern, target='./', cwd=os.curdir, exclude_dirs=()):
  """Search the root directory recursively for matching filenames.

  Args:
    pattern: the pattern used to match the filenames.
    target: the target directory to search.
    cwd: current working directory.
    exclude_dirs: Directories to not include when searching.

  Returns:
    A list of paths of the matched files.
  """
  # Backup the current working directory before changing it
  old_cwd = os.getcwd()
  os.chdir(cwd)

  matches = []
  for target, _, filenames in os.walk(target):
    if not any(target.startswith(e) for e in exclude_dirs):
      for filename in fnmatch.filter(filenames, pattern):
        matches.append(os.path.join(target, filename))

  # Restore the working directory
  os.chdir(old_cwd)

  return matches

def BuildAUTestTarball(buildroot, board, work_dir, version, archive_url):
  """Tar up the au test artifacts into the tarball_dir.

  Args:
    buildroot: Root directory where build occurs.
    board: Board type that was built on this machine.
    work_dir: Location for doing work.
    version: Basic version of the build i.e. 3289.23.0.
    archive_url: GS directory where we uploaded payloads.
  """
  au_test_tarball = os.path.join(work_dir, 'au_control.tar.bz2')

  cwd = os.path.join(buildroot, 'src', 'third_party', 'autotest', 'files')
  control_files_subdir = os.path.join('autotest', 'au_control_files')

  autotest_dir = os.path.join(work_dir, control_files_subdir)
  os.makedirs(autotest_dir)

  # Get basic version without R*.
  basic_version = re.search(r'R[0-9]+-([0-9][\w.]+)', version).group(1)

  # Pass in the python paths to the libs full release test needs.
  env_dict = dict(
      chromite_path=buildroot,
      devserver_path=os.path.join(buildroot, 'src', 'platform', 'dev'))

  python_path = '%(chromite_path)s:%(devserver_path)s' % env_dict
  cmd = ['site_utils/autoupdate/full_release_test.py',
         '--npo', '--nmo', '--dump',
         '--dump_dir', autotest_dir, '--archive_url', archive_url,
         basic_version, board, '--log=debug']

  gs_context_dir = os.path.dirname(gs.GSContext.GetDefaultGSUtilBin())
  run_env = None
  if not gs_context_dir in os.environ['PATH']:
    run_env = os.environ.copy()
    run_env['PATH'] += ':%s' % gs_context_dir
  else:
    run_env = os.environ

  run_env.setdefault('PYTHONPATH', '')
  run_env['PYTHONPATH'] += ':%s' % python_path

  cros_build_lib.RunCommand(cmd, env=run_env, cwd=cwd)
  BuildTarball(buildroot, [control_files_subdir], au_test_tarball, cwd=work_dir)
  return au_test_tarball


def BuildAutotestControlFilesTarball(buildroot, cwd, tarball_dir):
  """Tar up the autotest control files.

  Args:
    buildroot: Root directory where build occurs.
    cwd: Current working directory.
    tarball_dir: Location for storing autotest tarball.

  Returns:
    Path of the partial autotest control files tarball.
  """
  # Find the control files in autotest/
  control_files = FindFilesWithPattern('control*', target='autotest', cwd=cwd,
                                       exclude_dirs=['autotest/test_suites'])
  control_files_tarball = os.path.join(tarball_dir, 'control_files.tar')
  BuildTarball(buildroot, control_files, control_files_tarball, cwd=cwd,
               compressed=False)
  return control_files_tarball


def BuildAutotestPackagesTarball(buildroot, cwd, tarball_dir):
  """Tar up the autotest packages.

  Args:
    buildroot: Root directory where build occurs.
    cwd: Current working directory.
    tarball_dir: Location for storing autotest tarball.

  Returns:
    Path of the partial autotest packages tarball.
  """
  input_list = ['autotest/packages']
  packages_tarball = os.path.join(tarball_dir, 'autotest_packages.tar')
  BuildTarball(buildroot, input_list, packages_tarball, cwd=cwd,
               compressed=False)
  return packages_tarball


def BuildAutotestTestSuitesTarball(buildroot, cwd, tarball_dir):
  """Tar up the autotest test suite control files.

  Args:
    buildroot: Root directory where build occurs.
    cwd: Current working directory.
    tarball_dir: Location for storing autotest tarball.

  Returns:
    Path of the autotest test suites tarball.
  """
  test_suites_tarball = os.path.join(tarball_dir, 'test_suites.tar.bz2')
  BuildTarball(buildroot, ['autotest/test_suites'], test_suites_tarball,
               cwd=cwd)
  return test_suites_tarball


def BuildAutotestServerPackageTarball(buildroot, cwd, tarball_dir):
  """Tar up the autotest files required by the server package.

  Args:
    buildroot: Root directory where build occurs.
    cwd: Current working directory.
    tarball_dir: Location for storing autotest tarballs.

  Returns:
    The path of the autotest server package tarball.
  """
  # Find all files in autotest excluding certain directories.
  autotest_files = FindFilesWithPattern(
      '*', target='autotest', cwd=cwd,
      exclude_dirs=('autotest/packages', 'autotest/client/deps/',
                    'autotest/client/tests', 'autotest/client/site_tests'))
  tarball = os.path.join(tarball_dir, 'autotest_server_package.tar.bz2')
  BuildTarball(buildroot, autotest_files, tarball, cwd=cwd, error_code_ok=True)
  return tarball


def BuildFullAutotestTarball(buildroot, board, tarball_dir):
  """Tar up the full autotest directory into image_dir.

  Args:
    buildroot: Root directory where build occurs.
    board: Board type that was built on this machine.
    tarball_dir: Location for storing autotest tarballs.

  Returns:
    A tuple the path of the full autotest tarball.
  """

  tarball = os.path.join(tarball_dir, 'autotest.tar.bz2')
  cwd = os.path.abspath(os.path.join(buildroot, 'chroot', 'build', board,
                                     constants.AUTOTEST_BUILD_PATH, '..'))
  result = BuildTarball(buildroot, ['autotest'], tarball, cwd=cwd,
                        error_code_ok=True)

  # Emerging the autotest package to the factory test image while this is
  # running modifies the timestamp on /build/autotest/server by
  # adding a tmp directory underneath it.
  # When tar spots this, it flags this and returns
  # status code 1. The tarball is still OK, although there might be a few
  # unneeded (and garbled) tmp files. If tar fails in a different way, it'll
  # return an error code other than 1.
  # TODO: Fix the autotest ebuild. See http://crbug.com/237537
  if result.returncode not in (0, 1):
    raise Exception('Autotest tarball creation failed with exit code %s'
                    % (result.returncode))

  return tarball


def BuildImageZip(archive_dir, image_dir):
  """Build image.zip in archive_dir from contents of image_dir.

  Exclude the dev image from the zipfile.

  Args:
    archive_dir: Directory to store image.zip.
    image_dir: Directory to zip up.

  Returns:
    The basename of the zipfile.
  """
  filename = 'image.zip'
  zipfile = os.path.join(archive_dir, filename)
  cros_build_lib.RunCommand(['zip', zipfile, '-r', '.'], cwd=image_dir,
                            capture_output=True)
  return filename


def BuildStandaloneArchive(archive_dir, image_dir, artifact_info):
  """Create a compressed archive from the specified image information.

  The artifact info is derived from a JSON file in the board overlay. It
  should be in the following format:
  {
  "artifacts": [
    { artifact },
    { artifact },
    ...
  ]
  }
  Each artifact can contain the following keys:
  input - Required. A list of paths and globs that expands to
      the list of files to archive.
  output - the name of the archive to be created. If omitted,
      it will default to the first filename, stripped of
      extensions, plus the appropriate .tar.gz or other suffix.
  archive - "tar" or "zip". If omitted, files will be uploaded
      directly, without being archived together.
  compress - a value cros_build_lib.CompressionStrToType knows about. Only
      useful for tar. If omitted, an uncompressed tar will be created.

  Args:
    archive_dir: Directory to store image zip.
    image_dir: Base path for all inputs.
    artifact_info: Extended archive configuration dictionary containing:
      - paths - required, list of files to archive.
      - output, archive & compress entries from the JSON file.

  Returns:
    The base name of the archive.

  Raises:
    A ValueError if the compression or archive values are unknown.
    A KeyError is a required field is missing from artifact_info.
  """
  if 'archive' not in artifact_info:
    # Copy the file in 'paths' as is to the archive directory.
    if len(artifact_info['paths']) > 1:
      raise ValueError('default archive type does not support multiple inputs')
    src_image = os.path.join(image_dir, artifact_info['paths'][0])
    tgt_image = os.path.join(archive_dir, artifact_info['paths'][0])
    if not os.path.exists(tgt_image):
      # The image may have already been copied into place. If so, overwriting it
      # can affect parallel processes.
      shutil.copy(src_image, tgt_image)
    return artifact_info['paths']

  inputs = artifact_info['paths']
  archive = artifact_info['archive']
  compress = artifact_info.get('compress')
  compress_type = cros_build_lib.CompressionStrToType(compress)
  if compress_type is None:
    raise ValueError('unknown compression type: %s' % compress)

  # If the output is fixed, use that. Otherwise, construct it
  # from the name of the first archived file, stripping extensions.
  filename = artifact_info.get(
      'output', '%s.%s' % (os.path.splitext(inputs[0])[0], archive))
  if archive == 'tar':
    # Add the .compress extension if we don't have a fixed name.
    if 'output' not in artifact_info and compress:
      filename = "%s.%s" % (filename, compress)
    extra_env = {'XZ_OPT': '-1'}
    cros_build_lib.CreateTarball(
        os.path.join(archive_dir, filename), image_dir,
        inputs=inputs, compression=compress_type, extra_env=extra_env)
  elif archive == 'zip':
    cros_build_lib.RunCommand(
        ['zip', os.path.join(archive_dir, filename), '-r'] + inputs,
        cwd=image_dir, capture_output=True)
  else:
    raise ValueError('unknown archive type: %s' % archive)

  return [filename]


def BuildStrippedPackagesTarball(buildroot, board, package_globs, archive_dir):
  """Builds a tarball containing stripped packages.

  Args:
    buildroot: Root directory where build occurs.
    board: The board for which packages should be tarred up.
    package_globs: List of package search patterns. Each pattern is used to
        search for packages via `equery list`.
    archive_dir: The directory to drop the tarball in.

  Returns:
    The file name of the output tarball, None if no package found.
  """
  chroot_path = os.path.join(buildroot, constants.DEFAULT_CHROOT_DIR)
  board_path = os.path.join(chroot_path, 'build', board)
  stripped_pkg_dir = os.path.join(board_path, 'stripped-packages')
  tarball_paths = []
  for pattern in package_globs:
    packages = portage_util.FindPackageNameMatches(pattern, board,
                                                   buildroot=buildroot)
    for cpv in packages:
      pkg = '%s/%s' % (cpv.category, cpv.pv)
      cmd = ['strip_package', '--board', board, pkg]
      cros_build_lib.RunCommand(cmd, cwd=buildroot, enter_chroot=True)
      # Find the stripped package.
      files = glob.glob(os.path.join(stripped_pkg_dir, pkg) + '.*')
      if not files:
        raise AssertionError('Silent failure to strip binary %s? '
                             'Failed to find stripped files at %s.' %
                             (pkg, os.path.join(stripped_pkg_dir, pkg)))
      if len(files) > 1:
        logging.PrintBuildbotStepWarnings()
        logging.warning('Expected one stripped package for %s, found %d',
                        pkg, len(files))

      tarball = sorted(files)[-1]
      tarball_paths.append(os.path.abspath(tarball))

  if not tarball_paths:
    # tar barfs on an empty list of files, so skip tarring completely.
    return None

  tarball_output = os.path.join(archive_dir, 'stripped-packages.tar')
  BuildTarball(buildroot, tarball_paths, tarball_output, compressed=False)
  return os.path.basename(tarball_output)


def BuildGceTarball(archive_dir, image_dir, image):
  """Builds a tarball that can be converted into a GCE image.

  GCE has some very specific requirements about the format of VM
  images. The full list can be found at
  https://cloud.google.com/compute/docs/tutorials/building-images#requirements

  Args:
    archive_dir: Directory to store the output tarball.
    image_dir: Directory where raw disk file can be found.
    image: Name of raw disk file.

  Returns:
    The file name of the output tarball.
  """
  with osutils.TempDir() as tempdir:
    temp_disk_raw = os.path.join(tempdir, 'disk.raw')
    output = constants.ImageBinToGceTar(image)
    output_file = os.path.join(archive_dir, output)
    os.symlink(os.path.join(image_dir, image), temp_disk_raw)

    cros_build_lib.CreateTarball(
        output_file, tempdir, inputs=['disk.raw'],
        compression=cros_build_lib.COMP_GZIP, extra_args=['--dereference'])
    return os.path.basename(output_file)


def BuildFirmwareArchive(buildroot, board, archive_dir):
  """Build firmware_from_source.tar.bz2 in archive_dir from build root.

  Args:
    buildroot: Root directory where build occurs.
    board: Board name of build target.
    archive_dir: Directory to store output file.

  Returns:
    The basename of the archived file, or None if the target board does
    not have firmware from source.
  """
  firmware_root = os.path.join(buildroot, 'chroot', 'build', board, 'firmware')
  source_list = [os.path.relpath(f, firmware_root)
                 for f in glob.iglob(os.path.join(firmware_root, '*'))]
  if not source_list:
    return None

  archive_name = 'firmware_from_source.tar.bz2'
  archive_file = os.path.join(archive_dir, archive_name)
  BuildTarball(buildroot, source_list, archive_file, cwd=firmware_root)
  return archive_name


def BuildFactoryZip(buildroot, board, archive_dir, factory_shim_dir,
                    version=None):
  """Build factory_image.zip in archive_dir.

  Args:
    buildroot: Root directory where build occurs.
    board: Board name of build target.
    archive_dir: Directory to store factory_image.zip.
    factory_shim_dir: Directory containing factory shim.
    version: The version string to be included in the factory image.zip.

  Returns:
    The basename of the zipfile.
  """
  filename = 'factory_image.zip'

  # Creates a staging temporary folder.
  temp_dir = tempfile.mkdtemp(prefix='cbuildbot_factory')

  zipfile = os.path.join(archive_dir, filename)
  cmd = ['zip', '-r', zipfile, '.']

  # Rules for archive: { folder: pattern }
  rules = {
      factory_shim_dir:
          ['*factory_install*.bin', '*partition*',
           os.path.join('netboot', '*')],
  }

  for folder, patterns in rules.items():
    if not folder or not os.path.exists(folder):
      continue
    basename = os.path.basename(folder)
    target = os.path.join(temp_dir, basename)
    os.symlink(folder, target)
    for pattern in patterns:
      cmd.extend(['--include', os.path.join(basename, pattern)])

  # Everything in /usr/local/factory/bundle gets overlaid into the
  # bundle.
  bundle_src_dir = os.path.join(
      buildroot, 'chroot', 'build', board, 'usr', 'local', 'factory', 'bundle')
  if os.path.exists(bundle_src_dir):
    for f in os.listdir(bundle_src_dir):
      src_path = os.path.join(bundle_src_dir, f)
      os.symlink(src_path, os.path.join(temp_dir, f))
      cmd.extend(['--include',
                  f if os.path.isfile(src_path) else
                  os.path.join(f, '*')])

  # Add a version file in the zip file.
  if version is not None:
    version_file = os.path.join(temp_dir, 'BUILD_VERSION')
    osutils.WriteFile(version_file, version)
    cmd.extend(['--include', version_file])

  cros_build_lib.RunCommand(cmd, cwd=temp_dir, capture_output=True)
  osutils.RmDir(temp_dir)
  return filename


def ArchiveHWQual(buildroot, hwqual_name, archive_dir, image_dir):
  """Create a hwqual tarball in archive_dir.

  Args:
    buildroot: Root directory where build occurs.
    hwqual_name: Name for tarball.
    archive_dir: Local directory for hwqual tarball.
    image_dir: Directory containing test image.
  """
  scripts_dir = os.path.join(buildroot, 'src', 'scripts')
  ssh_private_key = os.path.join(image_dir, constants.TEST_KEY_PRIVATE)
  cmd = [os.path.join(scripts_dir, 'archive_hwqual'),
         '--from', archive_dir,
         '--image_dir', image_dir,
         '--ssh_private_key', ssh_private_key,
         '--output_tag', hwqual_name]
  cros_build_lib.RunCommand(cmd, capture_output=True)
  return '%s.tar.bz2' % hwqual_name


def CreateTestRoot(build_root):
  """Returns a temporary directory for test results in chroot.

  Returns:
    The path inside the chroot rather than whole path.
  """
  # Create test directory within tmp in chroot.
  chroot = os.path.join(build_root, 'chroot')
  chroot_tmp = os.path.join(chroot, 'tmp')
  test_root = tempfile.mkdtemp(prefix='cbuildbot', dir=chroot_tmp)

  # Path inside chroot.
  return os.path.sep + os.path.relpath(test_root, start=chroot)


def GeneratePayloads(build_root, target_image_path, archive_dir, full=False,
                     delta=False, stateful=False):
  """Generates the payloads for hw testing.

  Args:
    build_root: The root of the chromium os checkout.
    target_image_path: The path to the image to generate payloads to.
    archive_dir: Where to store payloads we generated.
    full: Generate full payloads.
    delta: Generate delta payloads.
    stateful: Generate stateful payload.
  """
  real_target = os.path.realpath(target_image_path)
  # The path to the target should look something like this:
  # .../link/R37-5952.0.2014_06_12_2302-a1/chromiumos_test_image.bin
  board, os_version = real_target.split('/')[-3:-1]
  prefix = 'chromeos'
  suffix = 'dev.bin'

  cwd = os.path.join(build_root, 'src', 'scripts')
  path = path_util.ToChrootPath(
      os.path.join(build_root, 'src', 'platform', 'dev', 'host'))
  chroot_dir = os.path.join(build_root, 'chroot')
  chroot_tmp = os.path.join(chroot_dir, 'tmp')
  chroot_target = path_util.ToChrootPath(target_image_path)

  with osutils.TempDir(base_dir=chroot_tmp,
                       prefix='generate_payloads') as temp_dir:
    chroot_temp_dir = temp_dir.replace(chroot_dir, '', 1)

    cmd = [
        os.path.join(path, 'cros_generate_update_payload'),
        '--image', chroot_target,
        '--output', os.path.join(chroot_temp_dir, 'update.gz')
    ]
    if full:
      cros_build_lib.RunCommand(cmd, enter_chroot=True, cwd=cwd)
      name = '_'.join([prefix, os_version, board, 'full', suffix])
      # Names for full payloads look something like this:
      # chromeos_R37-5952.0.2014_06_12_2302-a1_link_full_dev.bin
      shutil.move(os.path.join(temp_dir, 'update.gz'),
                  os.path.join(archive_dir, name))

    cmd.extend(['--src_image', chroot_target])
    if delta:
      cros_build_lib.RunCommand(cmd, enter_chroot=True, cwd=cwd)
      # Names for delta payloads look something like this:
      # chromeos_R37-5952.0.2014_06_12_2302-a1_R37-
      # 5952.0.2014_06_12_2302-a1_link_delta_dev.bin
      name = '_'.join([prefix, os_version, os_version, board, 'delta', suffix])
      shutil.move(os.path.join(temp_dir, 'update.gz'),
                  os.path.join(archive_dir, name))

    if stateful:
      cmd = [
          os.path.join(path, 'cros_generate_stateful_update_payload'),
          '--image', chroot_target,
          '--output', chroot_temp_dir
      ]
      cros_build_lib.RunCommand(cmd, enter_chroot=True, cwd=cwd)
      shutil.move(os.path.join(temp_dir, STATEFUL_FILE),
                  os.path.join(archive_dir, STATEFUL_FILE))


def GetChromeLKGM(revision):
  """Returns the ChromeOS LKGM from Chrome given the git revision."""
  revision = revision or 'refs/heads/master'
  lkgm_url_path = '%s/+/%s/%s?format=text' % (
      constants.CHROMIUM_SRC_PROJECT, revision, constants.PATH_TO_CHROME_LKGM)
  contents_b64 = gob_util.FetchUrl(site_config.params.EXTERNAL_GOB_HOST,
                                   lkgm_url_path)
  return base64.b64decode(contents_b64.read()).strip()


def SyncChrome(build_root, chrome_root, useflags, tag=None, revision=None):
  """Sync chrome.

  Args:
    build_root: The root of the chromium os checkout.
    chrome_root: The directory where chrome is stored.
    useflags: Array of use flags.
    tag: If supplied, the Chrome tag to sync.
    revision: If supplied, the Chrome revision to sync.
  """
  # --reset tells sync_chrome to blow away local changes and to feel
  # free to delete any directories that get in the way of syncing. This
  # is needed for unattended operation.
  sync_chrome = os.path.join(build_root, 'chromite', 'bin', 'sync_chrome')
  internal = constants.USE_CHROME_INTERNAL in useflags
  cmd = [sync_chrome, '--reset']
  cmd += ['--internal'] if internal else []
  cmd += ['--tag', tag] if tag is not None else []
  cmd += ['--revision', revision] if revision is not None else []
  cmd += [chrome_root]
  retry_util.RunCommandWithRetries(constants.SYNC_RETRIES, cmd, cwd=build_root)


def PatchChrome(chrome_root, patch, subdir):
  """Apply a patch to Chrome.

  Args:
    chrome_root: The directory where chrome is stored.
    patch: Rietveld issue number to apply.
    subdir: Subdirectory to apply patch in.
  """
  cmd = ['apply_issue', '-i', patch]
  cros_build_lib.RunCommand(cmd, cwd=os.path.join(chrome_root, subdir))


class ChromeSDK(object):
  """Wrapper for the 'cros chrome-sdk' command."""

  DEFAULT_JOBS = 24
  DEFAULT_JOBS_GOMA = 500

  def __init__(self, cwd, board, extra_args=None, chrome_src=None, goma=False,
               debug_log=True, cache_dir=None, target_tc=None,
               toolchain_url=None):
    """Initialization.

    Args:
      cwd: Where to invoke 'cros chrome-sdk'.
      board: The board to run chrome-sdk for.
      extra_args: Extra args to pass in on the command line.
      chrome_src: Path to pass in with --chrome-src.
      goma: If True, run using goma.
      debug_log: If set, run with debug log-level.
      cache_dir: Specify non-default cache directory.
      target_tc: Override target toolchain.
      toolchain_url: Override toolchain url pattern.
    """
    self.cwd = cwd
    self.board = board
    self.extra_args = extra_args or []
    if chrome_src:
      self.extra_args += ['--chrome-src', chrome_src]
    self.goma = goma
    if not self.goma:
      self.extra_args.append('--nogoma')
    self.debug_log = debug_log
    self.cache_dir = cache_dir
    self.target_tc = target_tc
    self.toolchain_url = toolchain_url

  def _GetDefaultTargets(self):
    """Get the default chrome targets to build."""
    targets = ['chrome', 'chrome_sandbox']

    use_flags = portage_util.GetInstalledPackageUseFlags(constants.CHROME_CP,
                                                         self.board)
    if 'nacl' in use_flags.get(constants.CHROME_CP, []):
      targets += ['nacl_helper']

    return targets

  def Run(self, cmd, extra_args=None):
    """Run a command inside the chrome-sdk context."""
    cros_cmd = ['cros']
    if self.debug_log:
      cros_cmd += ['--log-level', 'debug']
    if self.cache_dir:
      cros_cmd += ['--cache-dir', self.cache_dir]
    if self.target_tc:
      self.extra_args += ['--target-tc', self.target_tc]
    if self.toolchain_url:
      self.extra_args += ['--toolchain-url', self.toolchain_url]
    cros_cmd += ['chrome-sdk', '--board', self.board] + self.extra_args
    cros_cmd += (extra_args or []) + ['--'] + cmd
    cros_build_lib.RunCommand(cros_cmd, cwd=self.cwd)

  def Ninja(self, jobs=None, debug=False, targets=None):
    """Run 'ninja' inside a chrome-sdk context.

    Args:
      jobs: The number of -j jobs to run.
      debug: Whether to do a Debug build (defaults to Release).
      targets: The targets to compile.
    """
    if jobs is None:
      jobs = self.DEFAULT_JOBS_GOMA if self.goma else self.DEFAULT_JOBS
    if targets is None:
      targets = self._GetDefaultTargets()
    flavor = 'Debug' if debug else 'Release'
    cmd = ['ninja', '-C', 'out_%s/%s' % (self.board, flavor), '-j', str(jobs)]
    self.Run(cmd + list(targets))
