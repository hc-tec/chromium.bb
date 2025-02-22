# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Configuration options for various cbuildbot builders."""

from __future__ import print_function

import copy

from chromite.lib import config_lib
from chromite.lib import constants
from chromite.lib import factory


def GetDefaultWaterfall(build_config, is_release_branch):
  if not (build_config['important'] or build_config['master']):
    return None
  if build_config['branch']:
    return None
  b_type = build_config['build_type']

  # Build types that, absent other cues, go on the internal waterfall.
  INTERNAL_TYPES = (constants.PRE_CQ_LAUNCHER_TYPE, constants.TOOLCHAIN_TYPE)

  if config_lib.IsCanaryType(b_type):
    # If this is a canary build, it may fall on different waterfalls:
    # - If we're building for a release branch, it belongs on a release
    #   waterfall.
    # - Otherwise, it belongs on the internal waterfall.
    if is_release_branch:
      return constants.WATERFALL_RELEASE
    else:
      return constants.WATERFALL_INTERNAL
  elif config_lib.IsCQType(b_type):
    # A Paladin can appear on the public or internal waterfall depending on its
    # 'internal' status.
    return (constants.WATERFALL_INTERNAL if build_config['internal'] else
            constants.WATERFALL_EXTERNAL)
  elif config_lib.IsPFQType(b_type) or b_type in INTERNAL_TYPES:
    # These builder types belong on the internal waterfall.
    return constants.WATERFALL_INTERNAL
  else:
    # No default active waterfall.
    return None


class HWTestList(object):
  """Container for methods to generate HWTest lists."""

  def __init__(self, is_release_branch):
    self.is_release_branch = is_release_branch

  def DefaultList(self, **kwargs):
    """Returns a default list of HWTestConfig's for a build

    Args:
      *kwargs: overrides for the configs
    """
    # Number of tests running in parallel in the AU suite.
    AU_TESTS_NUM = 2
    # Number of tests running in parallel in the asynchronous canary
    # test suite
    ASYNC_TEST_NUM = 2

    # Set the number of machines for the au and qav suites. If we are
    # constrained in the number of duts in the lab, only give 1 dut to each.
    if (kwargs.get('num', constants.HWTEST_DEFAULT_NUM) >=
        constants.HWTEST_DEFAULT_NUM):
      au_dict = dict(num=AU_TESTS_NUM)
      async_dict = dict(num=ASYNC_TEST_NUM)
    else:
      au_dict = dict(num=1)
      async_dict = dict(num=1)

    au_kwargs = kwargs.copy()
    au_kwargs.update(au_dict)
    # Force au suite to run first.
    au_kwargs['priority'] = constants.HWTEST_CQ_PRIORITY

    async_kwargs = kwargs.copy()
    async_kwargs.update(async_dict)
    async_kwargs['priority'] = constants.HWTEST_POST_BUILD_PRIORITY
    async_kwargs['async'] = True
    async_kwargs['suite_min_duts'] = 1
    async_kwargs['timeout'] = config_lib.HWTestConfig.ASYNC_HW_TEST_TIMEOUT

    if self.is_release_branch:
      bvt_inline_kwargs = async_kwargs
    else:
      bvt_inline_kwargs = kwargs.copy()
      bvt_inline_kwargs['timeout'] = (
          config_lib.HWTestConfig.SHARED_HW_TEST_TIMEOUT)

    # BVT + AU suite.
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                    **bvt_inline_kwargs),
            config_lib.HWTestConfig(constants.HWTEST_AU_SUITE,
                                    blocking=True, **au_kwargs),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
                                    **async_kwargs),
            config_lib.HWTestConfig(constants.HWTEST_CANARY_SUITE,
                                    **async_kwargs)]

  def DefaultListCanary(self, **kwargs):
    """Returns a default list of config_lib.HWTestConfig's for a canary build.

    Args:
      *kwargs: overrides for the configs
    """
    # Set minimum_duts default to 4, which means that lab will check the
    # number of available duts to meet the minimum requirement before creating
    # the suite job for canary builds.
    kwargs.setdefault('minimum_duts', 4)
    kwargs.setdefault('file_bugs', True)
    return self.DefaultList(**kwargs)

  def AFDOList(self, **kwargs):
    """Returns a default list of HWTestConfig's for a AFDO build.

    Args:
      *kwargs: overrides for the configs
    """
    afdo_dict = dict(pool=constants.HWTEST_SUITES_POOL,
                     timeout=120 * 60, num=1, async=True, retry=False,
                     max_retries=None)
    afdo_dict.update(kwargs)
    return [config_lib.HWTestConfig('perf_v2', **afdo_dict)]

  def DefaultListNonCanary(self, **kwargs):
    """Return a default list of HWTestConfig's for a non-canary build.

    Optional arguments may be overridden in `kwargs`, except that
    the `blocking` setting cannot be provided.
    """
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE, **kwargs),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE, **kwargs)]

  def DefaultListCQ(self, **kwargs):
    """Return a default list of HWTestConfig's for a CQ build.

    Optional arguments may be overridden in `kwargs`, except that
    the `blocking` setting cannot be provided.
    """
    default_dict = dict(pool=constants.HWTEST_PALADIN_POOL,
                        timeout=config_lib.HWTestConfig.PALADIN_HW_TEST_TIMEOUT,
                        file_bugs=False, priority=constants.HWTEST_CQ_PRIORITY,
                        minimum_duts=4, offload_failures_only=True)
    # Allows kwargs overrides to default_dict for cq.
    default_dict.update(kwargs)
    return self.DefaultListNonCanary(**default_dict)

  def DefaultListPFQ(self, **kwargs):
    """Return a default list of HWTestConfig's for a PFQ build.

    Optional arguments may be overridden in `kwargs`, except that
    the `blocking` setting cannot be provided.
    """
    default_dict = dict(pool=constants.HWTEST_PFQ_POOL, file_bugs=True,
                        priority=constants.HWTEST_PFQ_PRIORITY,
                        retry=False, max_retries=None, minimum_duts=4)
    # Allows kwargs overrides to default_dict for pfq.
    default_dict.update(kwargs)
    return self.DefaultListNonCanary(**default_dict)

  def SharedPoolPFQ(self, **kwargs):
    """Return a list of HWTestConfigs for PFQ which uses a shared pool.

    The returned suites will run in pool:critical by default, which is
    shared with other types of builders (canaries, cq). The first suite in the
    list is a blocking sanity suite that verifies the build will not break dut.
    """
    sanity_dict = dict(pool=constants.HWTEST_MACH_POOL,
                       file_bugs=True, priority=constants.HWTEST_PFQ_PRIORITY,
                       retry=False, max_retries=None)
    sanity_dict.update(kwargs)
    sanity_dict.update(dict(num=1, minimum_duts=1, suite_min_duts=1,
                            blocking=True))
    default_dict = dict(pool=constants.HWTEST_MACH_POOL,
                        suite_min_duts=3)
    default_dict.update(kwargs)
    suite_list = [config_lib.HWTestConfig(constants.HWTEST_SANITY_SUITE,
                                          **sanity_dict)]
    suite_list.extend(self.DefaultListPFQ(**default_dict))
    return suite_list

  def DefaultListAndroidPFQ(self, **kwargs):
    """Return a default list of HWTestConfig's for a PFQ build.

    Optional arguments may be overridden in `kwargs`, except that
    the `blocking` setting cannot be provided.
    """
    default_dict = dict(file_bugs=True,
                        timeout=config_lib.HWTestConfig.ASYNC_HW_TEST_TIMEOUT,
                        priority=constants.HWTEST_PFQ_PRIORITY,
                        retry=True, max_retries=None, minimum_duts=3)
    # Allows kwargs overrides to default_dict for pfq.
    default_dict.update(kwargs)

    # TODO(crbug.com/610807): Disable the HWTests for now, since we are having
    # issues getting them to run and complete in time.
    # return [config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
    #                                num=8, pool=constants.HWTEST_MACH_POOL,
    #                                **default_dict),
    return [config_lib.HWTestConfig(constants.HWTEST_ARC_COMMIT_SUITE,
                                    num=3, pool=constants.HWTEST_MACH_POOL,
                                    **default_dict)]

  def SharedPoolAndroidPFQ(self, **kwargs):
    """Return a list of HWTestConfigs for PFQ which uses a shared pool.

    The returned suites will run in pool:critical by default, which is
    shared with other types of builders (canaries, cq). The first suite in the
    list is a blocking sanity suite that verifies the build will not break dut.
    """
    sanity_dict = dict(pool=constants.HWTEST_MACH_POOL,
                       file_bugs=True, priority=constants.HWTEST_PFQ_PRIORITY,
                       retry=False, max_retries=None)
    sanity_dict.update(kwargs)
    sanity_dict.update(dict(num=1, minimum_duts=1, suite_min_duts=1,
                            blocking=True))
    default_dict = dict(suite_min_duts=3)
    default_dict.update(kwargs)
    suite_list = [config_lib.HWTestConfig(constants.HWTEST_SANITY_SUITE,
                                          **sanity_dict)]
    suite_list.extend(self.DefaultListAndroidPFQ(**default_dict))
    return suite_list

  def SharedPoolCQ(self, **kwargs):
    """Return a list of HWTestConfigs for CQ which uses a shared pool.

    The returned suites will run in pool:critical by default, which is
    shared with other types of builder (canaries, pfq). The first suite in the
    list is a blocking sanity suite that verifies the build will not break dut.
    """
    sanity_dict = dict(pool=constants.HWTEST_MACH_POOL,
                       timeout=config_lib.HWTestConfig.SHARED_HW_TEST_TIMEOUT,
                       file_bugs=False, priority=constants.HWTEST_CQ_PRIORITY)
    sanity_dict.update(kwargs)
    sanity_dict.update(dict(num=1, minimum_duts=1, suite_min_duts=1,
                            blocking=True))
    default_dict = dict(pool=constants.HWTEST_MACH_POOL,
                        suite_min_duts=10)
    default_dict.update(kwargs)
    suite_list = [config_lib.HWTestConfig(constants.HWTEST_SANITY_SUITE,
                                          **sanity_dict)]
    suite_list.extend(self.DefaultListCQ(**default_dict))
    return suite_list

  def SharedPoolCanary(self, **kwargs):
    """Return a list of HWTestConfigs for Canary which uses a shared pool.

    The returned suites will run in pool:critical by default, which is
    shared with CQs. The first suite in the list is a blocking sanity suite
    that verifies the build will not break dut.
    """
    sanity_dict = dict(pool=constants.HWTEST_MACH_POOL, file_bugs=True)
    sanity_dict.update(kwargs)
    sanity_dict.update(dict(num=1, minimum_duts=1, suite_min_duts=1,
                            blocking=True))
    default_dict = dict(pool=constants.HWTEST_MACH_POOL,
                        suite_min_duts=6)
    default_dict.update(kwargs)
    suite_list = [config_lib.HWTestConfig(constants.HWTEST_SANITY_SUITE,
                                          **sanity_dict)]
    suite_list.extend(self.DefaultListCanary(**default_dict))
    return suite_list

  def AFDORecordTest(self, **kwargs):
    default_dict = dict(pool=constants.HWTEST_MACH_POOL,
                        warn_only=True, num=1, file_bugs=True,
                        timeout=constants.AFDO_GENERATE_TIMEOUT,
                        priority=constants.HWTEST_PFQ_PRIORITY)
    # Allows kwargs overrides to default_dict for cq.
    default_dict.update(kwargs)
    return config_lib.HWTestConfig(constants.HWTEST_AFDO_SUITE, **default_dict)

  def WiFiCellPoolPreCQ(self, **kwargs):
    """Return a list of HWTestConfigs which run wifi tests.

    This should be used by the ChromeOS WiFi team to ensure changes pass the
    wifi tests as a pre-cq sanity check.
    """
    default_dict = dict(pool=constants.HWTEST_WIFICELL_PRE_CQ_POOL,
                        file_bugs=False,
                        priority=constants.HWTEST_DEFAULT_PRIORITY,
                        retry=False, max_retries=None, minimum_duts=1)
    default_dict.update(kwargs)
    suite_list = [config_lib.HWTestConfig(constants.WIFICELL_PRE_CQ,
                                          **default_dict)]
    return suite_list

  def AsanTest(self, **kwargs):
    """Return a list of HWTESTConfigs which run asan tests."""
    default_dict = dict(pool=constants.HWTEST_MACH_POOL, file_bugs=False,
                        priority=constants.HWTEST_DEFAULT_PRIORITY)
    default_dict.update(kwargs)
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
                                    **default_dict)]

  def ToolchainTestFull(self, **kwargs):
    """Return full set of HWTESTConfigs to run toolchain correctness tests."""
    default_dict = dict(pool=constants.HWTEST_SUITES_POOL, async=False,
                        file_bugs=False,
                        priority=constants.HWTEST_DEFAULT_PRIORITY)
    default_dict.update(kwargs)
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_TOOLCHAIN_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig('security',
                                    **default_dict),
            config_lib.HWTestConfig('kernel_daily_regression',
                                    **default_dict),
            config_lib.HWTestConfig('kernel_daily_benchmarks',
                                    **default_dict)]

  def ToolchainTestMedium(self, machine_pool, **kwargs):
    """Return list of HWTESTConfigs to run toolchain LLVM correctness tests.

    Since the kernel is not built with LLVM, it makes no sense for the
    toolchain to run kernel tests on LLVM builds.
    """
    default_dict = dict(pool=machine_pool, async=False,
                        file_bugs=False,
                        priority=constants.HWTEST_DEFAULT_PRIORITY)
    default_dict.update(kwargs)
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_TOOLCHAIN_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig('security',
                                    **default_dict)]

  def ToolchainTestLight(self, **kwargs):
    """Return miminal list of HWTESTConfigs to run toolchain correctness tests.

    This is a minimum set of tests, currently for some x86 boards.
    """
    default_dict = dict(pool=constants.HWTEST_SUITES_POOL, async=False,
                        file_bugs=False,
                        priority=constants.HWTEST_DEFAULT_PRIORITY)
    default_dict.update(kwargs)
    return [config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                    **default_dict),
            config_lib.HWTestConfig(constants.HWTEST_COMMIT_SUITE,
                                    **default_dict)]


def append_useflags(useflags):
  """Used to append a set of useflags to existing useflags.

  Useflags that shadow prior use flags will cause the prior flag to be removed.
  (e.g. appending '-foo' to 'foo' will cause 'foo' to be removed)

  Usage:
    new_config = base_config.derive(useflags=append_useflags(['foo', '-bar'])

  Args:
    useflags: List of string useflags to append.
  """
  assert isinstance(useflags, (list, set))
  shadowed_useflags = {'-' + flag for flag in useflags
                       if not flag.startswith('-')}
  shadowed_useflags.update({flag[1:] for flag in useflags
                            if flag.startswith('-')})
  def handler(old_useflags):
    new_useflags = set(old_useflags or [])
    new_useflags.update(useflags)
    new_useflags.difference_update(shadowed_useflags)
    return sorted(list(new_useflags))

  return handler


def remove_images(unsupported_images):
  """Remove unsupported images when applying changes to a BuildConfig.

  Used similarly to append_useflags.

  Args:
    unsupported_images: A list of image names that should not be present
                        in the final build config.

  Returns:
    A callable suitable for use with BuildConfig.apply.
  """
  unsupported = set(unsupported_images)

  def handler(old_images):
    if not old_images:
      old_images = []
    return [i for i in old_images if i not in unsupported]

  return handler


TRADITIONAL_VM_TESTS_SUPPORTED = [
    config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
    config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE),
    config_lib.VMTestConfig(constants.CROS_VM_TEST_TYPE)]

#
# Define assorted constants describing various sets of boards.
#

# Base per-board configuration.
# Every board must appear in exactly 1 of the following sets.

_arm_internal_release_boards = frozenset([
    'arkham',
    'beaglebone',
    'beaglebone_servo',
    'daisy',
    'daisy_skate',
    'daisy_spring',
    'elm',
    'gale',
    'gru',
    'hana',
    'kevin',
    'kevin-tpm2',
    'nyan',
    'nyan_big',
    'nyan_blaze',
    'nyan_kitty',
    'oak',
    'peach_pi',
    'peach_pit',
    'smaug',
    'smaug-cheets',
    'storm',
    'veyron_fievel',
    'veyron_jaq',
    'veyron_jerry',
    'veyron_mickey',
    'veyron_mighty',
    'veyron_minnie',
    'veyron_pinky',
    'veyron_rialto',
    'veyron_shark',
    'veyron_speedy',
    'veyron_tiger',
    'whirlwind',
])

_arm_external_boards = frozenset([
    'arm-generic',
    'arm64-generic',
    'arm64-llvmpipe',
])

_x86_internal_release_boards = frozenset([
    'amd64-generic-goofy',
    'asuka',
    'auron',
    'auron_paine',
    'auron_yuna',
    'banjo',
    'banon',
    'buddy',
    'butterfly',
    'candy',
    'caroline',
    'cave',
    'celes',
    'celes-cheets',
    'chell',
    'chell-cheets',
    'clapper',
    'cyan',
    'edgar',
    'enguarde',
    'expresso',
    'falco',
    'falco_li',
    'gandof',
    'glados',
    'glados-cheets',
    'glimmer',
    'glimmer-cheets',
    'gnawty',
    'guado',
    'guado_labstation',
    'guado_moblab',
    'heli',
    'jecht',
    'kefka',
    'kip',
    'kunimitsu',
    'lakitu',
    'lakitu_next',
    'lars',
    'leon',
    'link',
    'lulu',
    'lulu-cheets',
    'lumpy',
    'mccloud',
    'monroe',
    'ninja',
    'orco',
    'panther',
    'parrot',
    'parrot_ivb',
    'pbody',
    'peppy',
    'pyro',
    'quawks',
    'rambi',
    'reef',
    'reks',
    'relm',
    'rikku',
    'samus',
    'sentry',
    'setzer',
    'slippy',
    'snappy',
    'squawks',
    'stout',
    'strago',
    'stumpy',
    'sumo',
    'swanky',
    'terra',
    'tidus',
    'tricky',
    'ultima',
    'umaro',
    'winky',
    'wizpig',
    'wolf',
    'x86-alex',
    'x86-alex_he',
    'x86-mario',
    'x86-zgb',
    'x86-zgb_he',
    'zako',
])

_x86_external_boards = frozenset([
    'amd64-generic',
    'x32-generic',
    'x86-generic',
])

# Every board should be in only 1 of the above sets.
_distinct_board_sets = [
    _arm_internal_release_boards,
    _arm_external_boards,
    _x86_internal_release_boards,
    _x86_external_boards,
]

_arm_full_boards = (_arm_internal_release_boards |
                    _arm_external_boards)
_x86_full_boards = (_x86_internal_release_boards |
                    _x86_external_boards)

_arm_boards = _arm_full_boards
_x86_boards = _x86_full_boards

_all_release_boards = (
    _arm_internal_release_boards |
    _x86_internal_release_boards
)
_all_full_boards = (
    _arm_full_boards |
    _x86_full_boards
)
_all_boards = (
    _x86_boards |
    _arm_boards
)

_arm_release_boards = _arm_internal_release_boards
_x86_release_boards = _x86_internal_release_boards

_internal_boards = _all_release_boards

# Board can appear in 1 or more of the following sets.
_brillo_boards = frozenset([
    'arkham',
    'gale',
    'storm',
    'whirlwind',
])

_cheets_boards = frozenset([
    'glados-cheets',
    'glimmer-cheets',
    'smaug-cheets',
    'celes-cheets',
    'chell-cheets',
    'lulu-cheets',
])

_cheets_x86_boards = _cheets_boards | frozenset([
    'auron_paine',
    'auron_yuna',
    'banon',
    'buddy',
    'cave',
    'celes',
    'chell',
    'cyan',
    'edgar',
    'gandof',
    'glados',
    'glimmer-cheets',
    'kunimitsu',
    'lars',
    'lulu',
    'pyro',
    'reef',
    'reks',
    'samus',
    'sentry',
    'setzer',
    'snappy',
    'strago',
    'terra',
    'ultima',
    'umaro',
    'wizpig',
])

_beaglebone_boards = frozenset([
    'beaglebone',
    'beaglebone_servo',
])

_lakitu_boards = frozenset([
    'lakitu',
    'lakitu_next',
])

_moblab_boards = frozenset([
    'guado_moblab',
])

_nofactory_boards = _lakitu_boards | frozenset([
    'smaug',
])

_toolchains_from_source = frozenset([
    'x32-generic',
])

_noimagetest_boards = _lakitu_boards

_nohwqual_boards = _lakitu_boards

_norootfs_verification_boards = frozenset([
])

_base_layout_boards = _lakitu_boards

_no_unittest_boards = frozenset((
))

_cheets_vmtest_boards = frozenset([
    'cyan',
])

_no_vmtest_boards = (_arm_boards | _brillo_boards |
                     _cheets_x86_boards - _cheets_vmtest_boards)

# List of boards that run VMTests but only the smoke tests, not the AU tests
# until b/31341543 has been fixed.
_smoke_only_vmtest_boards = frozenset([
    'cyan',
])

# This is a list of configs that should be included on the main waterfall, but
# aren't included by default (see IsDefaultMainWaterfall). This loosely
# corresponds to the set of experimental or self-standing configs.
_waterfall_config_map = {
    constants.WATERFALL_EXTERNAL: frozenset([
        # Incremental
        'amd64-generic-incremental',
        'daisy-incremental',
        'x86-generic-incremental',

        # Full
        'amd64-generic-full',
        'arm-generic-full',
        'daisy-full',
        'oak-full',
        'x86-generic-full',

        # ASAN
        'amd64-generic-asan',
        'x86-generic-asan',
    ]),

    constants.WATERFALL_INTERNAL: frozenset([
        # Experimental Paladins.
        'reef-paladin',
        'gale-paladin',
        'lakitu_next-paladin',

        # Incremental Builders.
        'mario-incremental',
        'lakitu-incremental',
        'lakitu_next-incremental',

        # Firmware Builders.
        'link-depthcharge-full-firmware',
    ]),
}


def SiteParameters():
  """Create the site parameters for this site.

  Returns:
    dict containing SiteParameters for ChromeOS.
  """
  # Chrome OS site parameters.
  site_params = config_lib.DefaultSiteParameters()

  # Helpers for constructing Chrome OS site parameters.
  manifest_project = 'chromiumos/manifest'
  manifest_int_project = 'chromeos/manifest-internal'
  external_remote = 'cros'
  internal_remote = 'cros-internal'
  chromium_remote = 'chromium'
  chrome_remote = 'chrome'
  aosp_remote = 'aosp'
  weave_remote = 'weave'

  # Gerrit instance site parameters.
  site_params.update(
      config_lib.GerritInstanceParameters('EXTERNAL', 'chromium'))
  site_params.update(
      config_lib.GerritInstanceParameters('INTERNAL', 'chrome-internal'))
  site_params.update(
      config_lib.GerritInstanceParameters('AOSP', 'android'))
  site_params.update(
      config_lib.GerritInstanceParameters('WEAVE', 'weave'))

  site_params.update(
      # Parameters to define which manifests to use.
      MANIFEST_PROJECT=manifest_project,
      MANIFEST_INT_PROJECT=manifest_int_project,
      MANIFEST_PROJECTS=(manifest_project, manifest_int_project),
      MANIFEST_URL='%s/%s' % (
          site_params['EXTERNAL_GOB_URL'], manifest_project
      ),
      MANIFEST_INT_URL='%s/%s' % (
          site_params['INTERNAL_GERRIT_URL'], manifest_int_project
      ),

      # CrOS remotes specified in the manifests.
      EXTERNAL_REMOTE=external_remote,
      INTERNAL_REMOTE=internal_remote,
      GOB_REMOTES={
          site_params['EXTERNAL_GOB_INSTANCE']: external_remote,
          site_params['INTERNAL_GOB_INSTANCE']: internal_remote
      },
      CHROMIUM_REMOTE=chromium_remote,
      CHROME_REMOTE=chrome_remote,
      AOSP_REMOTE=aosp_remote,
      WEAVE_REMOTE=weave_remote,

      # Only remotes listed in CROS_REMOTES are considered branchable.
      # CROS_REMOTES and BRANCHABLE_PROJECTS must be kept in sync.
      GERRIT_HOSTS={
          external_remote: site_params['EXTERNAL_GERRIT_HOST'],
          internal_remote: site_params['INTERNAL_GERRIT_HOST'],
          aosp_remote: site_params['AOSP_GERRIT_HOST'],
          weave_remote: site_params['WEAVE_GERRIT_HOST']
      },
      CROS_REMOTES={
          external_remote: site_params['EXTERNAL_GOB_URL'],
          internal_remote: site_params['INTERNAL_GOB_URL'],
          aosp_remote: site_params['AOSP_GOB_URL'],
          weave_remote: site_params['WEAVE_GOB_URL']
      },
      GIT_REMOTES={
          chromium_remote: site_params['EXTERNAL_GOB_URL'],
          chrome_remote: site_params['INTERNAL_GOB_URL'],
          external_remote: site_params['EXTERNAL_GOB_URL'],
          internal_remote: site_params['INTERNAL_GOB_URL'],
          aosp_remote: site_params['AOSP_GOB_URL'],
          weave_remote: site_params['WEAVE_GOB_URL']
      },

      # Prefix to distinguish internal and external changes. This is used
      # when a user specifies a patch with "-g", when generating a key for
      # a patch to use in our PatchCache, and when displaying a custom
      # string for the patch.
      CHANGE_PREFIX={
          external_remote: site_params['EXTERNAL_CHANGE_PREFIX'],
          internal_remote: site_params['INTERNAL_CHANGE_PREFIX'],
      },

      # List of remotes that are okay to include in the external manifest.
      EXTERNAL_REMOTES=(
          external_remote, chromium_remote, aosp_remote
      ),

      # Mapping 'remote name' -> regexp that matches names of repositories on
      # that remote that can be branched when creating CrOS branch.
      # Branching script will actually create a new git ref when branching
      # these projects. It won't attempt to create a git ref for other projects
      # that may be mentioned in a manifest. If a remote is missing from this
      # dictionary, all projects on that remote are considered to not be
      # branchable.
      BRANCHABLE_PROJECTS={
          external_remote: r'(chromiumos|aosp)/(.+)',
          internal_remote: r'chromeos/(.+)',
      },

      # Additional parameters used to filter manifests, create modified
      # manifests, and to branch manifests.
      MANIFEST_VERSIONS_GOB_URL=(
          '%s/chromiumos/manifest-versions' % site_params['EXTERNAL_GOB_URL']
      ),
      MANIFEST_VERSIONS_INT_GOB_URL=(
          '%s/chromeos/manifest-versions' % site_params['INTERNAL_GOB_URL']
      ),
      MANIFEST_VERSIONS_GOB_URL_TEST=(
          '%s/chromiumos/manifest-versions-test' % (
              site_params['EXTERNAL_GOB_URL']
          )
      ),
      MANIFEST_VERSIONS_INT_GOB_URL_TEST=(
          '%s/chromeos/manifest-versions-test' % site_params['INTERNAL_GOB_URL']
      ),
      MANIFEST_VERSIONS_GS_URL='gs://chromeos-manifest-versions',

      # Standard directories under buildroot for cloning these repos.
      EXTERNAL_MANIFEST_VERSIONS_PATH='manifest-versions',
      INTERNAL_MANIFEST_VERSIONS_PATH='manifest-versions-internal',

      # URL of the repo project.
      REPO_URL='%s/external/repo' % site_params['EXTERNAL_GOB_URL']
  )

  return site_params


def DefaultSettings(site_params):
  """Create the default build config values for this site.

  Args:
    site_params: A populated config_lib.SiteParameters instance.

  Returns:
    dict: of default config_lib.BuildConfig values to use for this site.
  """
  # Site specific adjustments for default BuildConfig values.
  defaults = config_lib.DefaultSettings()

  # Git repository URL for our manifests.
  #  https://chromium.googlesource.com/chromiumos/manifest
  #  https://chrome-internal.googlesource.com/chromeos/manifest-internal
  defaults['manifest_repo_url'] = site_params['MANIFEST_URL']

  return defaults


def CreateBuilderTemplates(site_config, hw_test_list, is_release_branch):
  """CreateBuilderTemplates defines all BuildConfig templates.

  Args:
    site_config: A SiteConfig object to add the templates too.
    hw_test_list: Object to help create lists of standard HW Tests.
    is_release_branch: True if config for a release branch, False for TOT.
  """
  site_config.AddTemplate(
      'default_hw_tests_override',
      hw_tests_override=hw_test_list.DefaultList(
          num=constants.HWTEST_TRYBOT_NUM, pool=constants.HWTEST_TRYBOT_POOL,
          file_bugs=False),
  )

  # Config parameters for builders that do not run tests on the builder.
  site_config.AddTemplate(
      'no_unittest_builder',
      unittests=False,
  )

  site_config.AddTemplate(
      'no_vmtest_builder',
      vm_tests=[],
      vm_tests_override=None,
      run_gce_tests=False,
  )

  site_config.AddTemplate(
      'smoke_only_vmtest_builder',
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE)],
      vm_tests_override=None,
      run_gce_tests=False,
  )

  site_config.AddTemplate(
      'no_hwtest_builder',
      hw_tests=[],
      hw_tests_override=[],
  )

  # Builder type templates.

  site_config.AddTemplate(
      'full',
      site_config.templates.default_hw_tests_override,
      # Full builds are test builds to show that we can build from scratch,
      # so use settings to build from scratch, and archive the results.
      usepkg_build_packages=False,
      chrome_sdk=True,

      build_type=constants.BUILD_FROM_SOURCE_TYPE,
      archive_build_debug=True,
      images=['base', 'recovery', 'test', 'factory_install'],
      git_sync=True,
      trybot_list=True,
      description='Full Builds',
      image_test=True,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Continuous',
  )

  site_config.AddTemplate(
      'pfq',
      build_type=constants.PFQ_TYPE,
      build_timeout=20 * 60,
      important=True,
      uprev=True,
      overlays=constants.PUBLIC_OVERLAYS,
      manifest_version=True,
      trybot_list=True,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Chrome-PFQ',
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
                config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE)],
      vm_tests_override=TRADITIONAL_VM_TESTS_SUPPORTED,
  )

  site_config.AddTemplate(
      'paladin',
      hw_tests_override=hw_test_list.DefaultListNonCanary(
          num=constants.HWTEST_TRYBOT_NUM, pool=constants.HWTEST_TRYBOT_POOL,
          file_bugs=False),
      chroot_replace=False,
      important=True,
      build_type=constants.PALADIN_TYPE,
      overlays=constants.PUBLIC_OVERLAYS,
      prebuilts=constants.PUBLIC,
      manifest_version=True,
      trybot_list=True,
      description='Commit Queue',
      upload_standalone_images=False,
      images=['base', 'test'],
      image_test=True,
      chrome_sdk=True,
      chrome_sdk_build_chrome=False,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#TOC-CQ',

      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE)],
      vm_tests_override=TRADITIONAL_VM_TESTS_SUPPORTED,
  )

  # Incremental builders are intended to test the developer workflow.
  # For that reason, they don't uprev.
  site_config.AddTemplate(
      'incremental',
      site_config.templates.default_hw_tests_override,
      build_type=constants.INCREMENTAL_TYPE,
      chroot_replace=False,
      uprev=False,
      overlays=constants.PUBLIC_OVERLAYS,
      description='Incremental Builds',
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Continuous',
  )

  site_config.AddTemplate(
      'external',
      internal=False,
      overlays=constants.PUBLIC_OVERLAYS,
      manifest_repo_url=site_config.params['MANIFEST_URL'],
      manifest=constants.DEFAULT_MANIFEST,
  )

  # This builds with more source available.
  site_config.AddTemplate(
      'internal',
      internal=True,
      overlays=constants.BOTH_OVERLAYS,
      manifest_repo_url=site_config.params['MANIFEST_INT_URL'],
  )

  site_config.AddTemplate(
      'brillo',
      sync_chrome=False,
      chrome_sdk=False,
      afdo_use=False,
      dev_installer_prebuilts=False,
      # TODO(gauravsh): crbug.com/356414 Start running tests on Brillo configs.
      vm_tests=[],
  )

  site_config.AddTemplate(
      'lakitu',
      sync_chrome=False,
      chrome_sdk=False,
      afdo_use=False,
      dev_installer_prebuilts=False,
      vm_tests=[],
      vm_tests_override=None,
      hw_tests=[],
  )

  # An anchor of Laktiu' test customizations.
  site_config.AddTemplate(
      'lakitu_test_customizations',
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
                config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE)],
      run_gce_tests=True,
  )

  site_config.AddTemplate(
      'moblab',
      image_test=False,
      vm_tests=[],
  )

  site_config.AddTemplate(
      'beaglebone',
      site_config.templates.brillo,
      image_test=False,
      rootfs_verification=False,
      paygen=False,
      signer_tests=False,
      images=remove_images(['recovery', 'factory_install']),
  )

  # This adds Chrome branding.
  site_config.AddTemplate(
      'official_chrome',
      useflags=append_useflags([constants.USE_CHROME_INTERNAL]),
  )

  # This sets chromeos_official.
  site_config.AddTemplate(
      'official',
      site_config.templates.official_chrome,
      chromeos_official=True,
  )

  site_config.AddTemplate(
      'asan',
      site_config.templates.default_hw_tests_override,
      profile='asan',
      disk_layout='2gb-rootfs',
      # TODO(deymo): ASan builders generate bigger files, in particular a bigger
      # Chrome binary, that update_engine can't handle in delta payloads due to
      # memory limits. Remove the following lines once crbug.com/329248 is
      # fixed.
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE)],
      vm_tests_override=None,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-ASAN',
  )

  site_config.AddTemplate(
      'telemetry',
      site_config.templates.default_hw_tests_override,
      build_type=constants.INCREMENTAL_TYPE,
      uprev=False,
      overlays=constants.PUBLIC_OVERLAYS,
      vm_tests=[config_lib.VMTestConfig(constants.TELEMETRY_SUITE_TEST_TYPE,
                                        # Add an extra 60 minutes.
                                        timeout=120 * 60)],
      description='Telemetry Builds',
  )

  site_config.AddTemplate(
      'external_chromium_pfq',
      site_config.templates.default_hw_tests_override,
      build_type=constants.CHROME_PFQ_TYPE,
      important=True,
      uprev=False,
      overlays=constants.PUBLIC_OVERLAYS,
      manifest_version=True,
      chrome_rev=constants.CHROME_REV_LATEST,
      chrome_sdk=True,
      unittests=False,
      description='Preflight Chromium Uprev & Build (public)',
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
                config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE)],
      vm_tests_override=None,
  )

  # TODO(davidjames): Convert this to an external config once the unified master
  # logic is ready.
  site_config.AddTemplate(
      'chromium_pfq',
      site_config.templates.internal,
      site_config.templates.external_chromium_pfq,
      description='Preflight Chromium Uprev & Build (internal)',
      overlays=constants.BOTH_OVERLAYS,
      prebuilts=constants.PUBLIC,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Chrome-PFQ',
  )

  site_config.AddTemplate(
      'chrome_pfq',
      site_config.templates.chromium_pfq,
      site_config.templates.official,
      important=True,
      overlays=constants.BOTH_OVERLAYS,
      description='Preflight Chrome Uprev & Build (internal)',
      prebuilts=constants.PRIVATE,
  )

  site_config.AddTemplate(
      'chrome_try',
      build_type=constants.CHROME_PFQ_TYPE,
      chrome_rev=constants.CHROME_REV_TOT,
      important=False,
      manifest_version=False,
  )

  site_config.AddTemplate(
      'chromium_pfq_informational',
      site_config.templates.external_chromium_pfq,
      site_config.templates.chrome_try,
      chrome_sdk=False,
      unittests=False,
      description='Informational Chromium Uprev & Build (public)',
  )

  site_config.AddTemplate(
      'chromium_pfq_informational_gn',
      site_config.templates.chromium_pfq_informational,
      useflags=append_useflags(['gn']),
  )

  site_config.AddTemplate(
      'chrome_pfq_informational',
      site_config.templates.chromium_pfq_informational,
      site_config.templates.internal,
      site_config.templates.official,
      unittests=False,
      description='Informational Chrome Uprev & Build (internal)',
  )

  site_config.AddTemplate(
      'chrome_pfq_informational_gn',
      site_config.templates.chrome_pfq_informational,
      useflags=append_useflags(['gn']),
  )

  site_config.AddTemplate(
      'chrome_pfq_cheets_informational',
      site_config.templates.chrome_pfq_informational,
      hw_tests=hw_test_list.SharedPoolAndroidPFQ(),
      hw_tests_override=hw_test_list.SharedPoolAndroidPFQ()
  )

  site_config.AddTemplate(
      'chrome_perf',
      site_config.templates.chrome_pfq_informational,
      site_config.templates.no_unittest_builder,
      site_config.templates.no_vmtest_builder,
      description='Chrome Performance test bot',
      hw_tests=[config_lib.HWTestConfig(
          'perf_v2', pool=constants.HWTEST_CHROME_PERF_POOL,
          timeout=90 * 60, critical=True, num=1)],
      use_chrome_lkgm=True,
      useflags=append_useflags(['-cros-debug']),
  )

  site_config.AddTemplate(
      'tot_asan_informational',
      site_config.templates.chromium_pfq_informational,
      site_config.templates.asan,
      unittests=True,
      description='Build TOT Chrome with Address Sanitizer (Clang)',
  )

  # Because branch directories may be shared amongst builders on multiple
  # branches, they must delete the chroot every time they run.
  # They also potentially need to build [new] Chrome.
  site_config.AddTemplate(
      'pre_flight_branch',
      site_config.templates.internal,
      site_config.templates.official_chrome,
      site_config.templates.pfq,
      overlays=constants.BOTH_OVERLAYS,
      prebuilts=constants.PRIVATE,
      branch=True,
      trybot_list=False,
      sync_chrome=True,
      active_waterfall=constants.WATERFALL_RELEASE)

  site_config.AddTemplate(
      'internal_paladin',
      site_config.templates.paladin,
      site_config.templates.internal,
      site_config.templates.official_chrome,
      manifest=constants.OFFICIAL_MANIFEST,
      overlays=constants.BOTH_OVERLAYS,
      prebuilts=constants.PRIVATE,
      vm_tests=[],
      description=site_config.templates.paladin.description + ' (internal)',
  )

  # Used for paladin builders with nowithdebug flag (a.k.a -cros-debug)
  site_config.AddTemplate(
      'internal_nowithdebug_paladin',
      site_config.templates.internal_paladin,
      useflags=append_useflags(['-cros-debug']),
      description=(site_config.templates.paladin.description +
                   ' (internal, nowithdebug)'),
      prebuilts=False,
  )

  # Internal incremental builders don't use official chrome because we want
  # to test the developer workflow.
  site_config.AddTemplate(
      'internal_incremental',
      site_config.templates.internal,
      site_config.templates.incremental,
      overlays=constants.BOTH_OVERLAYS,
      description='Incremental Builds (internal)',
  )

  # A test-ap image is just a test image with a special profile enabled.
  # Note that each board enabled for test-ap use has to have the testbed-ap
  # profile linked to from its private overlay.
  site_config.AddTemplate(
      'test_ap',
      site_config.templates.internal,
      site_config.templates.default_hw_tests_override,
      build_type=constants.INCREMENTAL_TYPE,
      description='WiFi AP images used in testing',
      profile='testbed-ap',
      vm_tests=[],
  )

  # Create tryjob build configs to help with stress testing.
  site_config.AddTemplate(
      'unittest_stress',
      site_config.templates.no_vmtest_builder,
      site_config.templates.no_hwtest_builder,
      build_type=constants.TRYJOB_TYPE,
      description='Run Unittests repeatedly to look for flake.',

      builder_class_name='test_builders.UnittestStressBuilder',
      active_waterfall=constants.WATERFALL_TRYBOT,

      # Make this available, so we can stress a previous build.
      manifest_version=True,
  )

  site_config.AddTemplate(
      'release',
      site_config.templates.full,
      site_config.templates.official,
      site_config.templates.internal,
      site_config.templates.default_hw_tests_override,
      build_type=constants.CANARY_TYPE,
      build_timeout=12 * 60 * 60 if is_release_branch else (7 * 60 + 50) * 60,
      useflags=append_useflags(['-cros-debug']),
      afdo_use=True,
      manifest=constants.OFFICIAL_MANIFEST,
      manifest_version=True,
      images=['base', 'recovery', 'test', 'factory_install'],
      sign_types=['recovery'],
      push_image=True,
      upload_symbols=True,
      binhost_bucket='gs://chromeos-dev-installer',
      binhost_key='RELEASE_BINHOST',
      binhost_base_url='https://commondatastorage.googleapis.com/'
                       'chromeos-dev-installer',
      dev_installer_prebuilts=True,
      git_sync=False,
      vm_tests=[
          config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
          config_lib.VMTestConfig(constants.DEV_MODE_TEST_TYPE),
          config_lib.VMTestConfig(constants.CROS_VM_TEST_TYPE)],
      # Some release builders disable VMTests to be able to build on GCE, but
      # still want VMTests enabled on trybot builders.
      vm_tests_override=[
          config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
          config_lib.VMTestConfig(constants.DEV_MODE_TEST_TYPE),
          config_lib.VMTestConfig(constants.CROS_VM_TEST_TYPE)],
      hw_tests=hw_test_list.SharedPoolCanary(),
      paygen=True,
      signer_tests=True,
      trybot_list=True,
      hwqual=True,
      description="Release Builds (canary) (internal)",
      chrome_sdk=True,
      image_test=True,
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Canaries',
  )

  ### Release AFDO configs.

  site_config.AddTemplate(
      'release_afdo',
      site_config.templates.release,
      trybot_list=False,
      hw_tests=(
          hw_test_list.DefaultList(pool=constants.HWTEST_SUITES_POOL, num=4) +
          hw_test_list.AFDOList()
      ),
      push_image=False,
      paygen=False,
      dev_installer_prebuilts=False,
  )

  site_config.AddTemplate(
      'release_afdo_generate',
      site_config.templates.release_afdo,
      afdo_generate_min=True,
      afdo_use=False,
      afdo_update_ebuild=True,

      hw_tests=[hw_test_list.AFDORecordTest()],
      hw_tests_override=[hw_test_list.AFDORecordTest(
          num=constants.HWTEST_TRYBOT_NUM,
          pool=constants.HWTEST_TRYBOT_POOL,
          file_bugs=False,
          priority=constants.HWTEST_DEFAULT_PRIORITY,
      )],
  )

  site_config.AddTemplate(
      'release_afdo_use',
      site_config.templates.release_afdo,
      afdo_use=True,
  )

  site_config.AddTemplate(
      'moblab_release',
      site_config.templates.release,
      description='Moblab release builders',
      images=['base', 'recovery', 'test'],
      paygen_skip_delta_payloads=True,
      # TODO: re-enable paygen testing when crbug.com/386473 is fixed.
      paygen_skip_testing=True,
      important=False,
      afdo_use=False,
      signer_tests=False,
      hw_tests=[
          config_lib.HWTestConfig(constants.HWTEST_MOBLAB_SUITE,
                                  num=1, timeout=120*60),
          config_lib.HWTestConfig(constants.HWTEST_BVT_SUITE,
                                  warn_only=True, num=1),
          config_lib.HWTestConfig(constants.HWTEST_AU_SUITE,
                                  warn_only=True, num=1)],
  )

  site_config.AddTemplate(
      'cheets_release',
      site_config.templates.release,
      description='Cheets release builders',
      hw_tests=[
          config_lib.HWTestConfig(constants.HWTEST_ARC_COMMIT_SUITE, num=1),
          config_lib.HWTestConfig(constants.HWTEST_AU_SUITE,
                                  warn_only=True, num=1)],
  )

  # Factory and Firmware releases much inherit from these classes.
  # Modifications for these release builders should go here.

  # Naming conventions also must be followed. Factory and firmware branches
  # must end in -factory or -firmware suffixes.

  site_config.AddTemplate(
      'factory',
      site_config.templates.release,
      site_config.templates.no_hwtest_builder,
      site_config.templates.no_vmtest_builder,
      afdo_use=False,
      chrome_sdk=False,
      chrome_sdk_build_chrome=False,
      description='Factory Builds',
      factory_toolkit=True,
      hwqual=False,
      images=['test', 'factory_install'],
      image_test=False,
      paygen=False,
      signer_tests=False,
      sign_types=['factory'],
      upload_hw_test_artifacts=False,
      upload_symbols=False,
  )

  site_config.AddTemplate(
      'firmware_base',
      site_config.templates.no_vmtest_builder,
      images=[],
      factory_toolkit=False,
      packages=['virtual/chromeos-firmware', 'chromeos-base/autotest-all'],
      usepkg_build_packages=True,
      sync_chrome=False,
      chrome_sdk=False,
      unittests=False,
      hw_tests=[],
      dev_installer_prebuilts=False,
      upload_hw_test_artifacts=True,
      upload_symbols=False,
      useflags=append_useflags(['chromeless_tty']),
      signer_tests=False,
      trybot_list=False,
      paygen=False,
      image_test=False,
      sign_types=['firmware'],
  )

  site_config.AddTemplate(
      'firmware',
      site_config.templates.release,
      site_config.templates.firmware_base,
      description='Firmware Canary',
      manifest=constants.DEFAULT_MANIFEST,
      afdo_use=False,
  )

  site_config.AddTemplate(
      'depthcharge_firmware',
      site_config.templates.firmware,
      useflags=append_useflags(['depthcharge']))

  site_config.AddTemplate(
      'depthcharge_full_firmware',
      site_config.templates.full,
      site_config.templates.internal,
      site_config.templates.firmware_base,
      useflags=append_useflags(['depthcharge']),
      description='Firmware Informational',
  )

  site_config.AddTemplate(
      'payloads',
      site_config.templates.internal,
      site_config.templates.no_vmtest_builder,
      site_config.templates.no_unittest_builder,
      site_config.templates.no_hwtest_builder,
      build_type=constants.PAYLOADS_TYPE,
      builder_class_name='release_builders.GeneratePayloadsBuilder',
      description='Regenerate release payloads.',
      # Sync to the code used to do the build the first time.
      manifest_version=True,
      # This is the actual work we want to do.
      paygen=True,
      upload_hw_test_artifacts=False,
      active_waterfall=constants.WATERFALL_TRYBOT,
  )


def CreateBoardConfigs(site_config, ge_build_config):
  """Create mixin templates for each board."""
  # Extract the full list of board names from GE data.
  board_names = set(config_lib.GeBuildConfigAllBoards(ge_build_config))

  # TODO(crbug.com/648473): Remove these, after GE adds them to their data set.
  board_names = board_names.union(_all_boards)

  result = dict()
  for board in board_names:
    board_config = config_lib.BuildConfig(boards=[board])

    if board in _internal_boards:
      board_config.update(site_config.templates.internal)
      board_config.update(site_config.templates.official_chrome)
      board_config.update(manifest=constants.OFFICIAL_MANIFEST)
    if board in _brillo_boards:
      board_config.update(site_config.templates.brillo)
    if board in _lakitu_boards:
      board_config.update(site_config.templates.lakitu)
    if board in _moblab_boards:
      board_config.update(site_config.templates.moblab)
    if board in _nofactory_boards:
      board_config.update(factory=False)
      board_config.update(factory_toolkit=False)
      board_config.update(factory_install_netboot=False)
      board_config.update(images=remove_images(['factory_install']))
    if board in _toolchains_from_source:
      board_config.update(usepkg_toolchain=False)
    if board in _noimagetest_boards:
      board_config.update(image_test=False)
    if board in _nohwqual_boards:
      board_config.update(hwqual=False)
    if board in _norootfs_verification_boards:
      board_config.update(rootfs_verification=False)
    if board in _base_layout_boards:
      board_config.update(disk_layout='base')
    if board in _no_unittest_boards:
      board_config.update(site_config.templates.no_unittest_builder)
    if board in _no_vmtest_boards:
      board_config.update(site_config.templates.no_vmtest_builder)
    if board in _smoke_only_vmtest_boards:
      board_config.update(site_config.templates.smoke_only_vmtest_builder)
    if board in _beaglebone_boards:
      board_config.apply(site_config.templates.beaglebone)

    result[board] = board_config

  return result


def ToolchainBuilders(site_config, board_configs, hw_test_list):
  """Define templates used for toolchain builders.

  Args:
    site_config: config_lib.SiteConfig to be modified by adding templates
                 and configs.
    board_configs: Dictionary mapping board names to per-board configurations.
    hw_test_list: Object to help create lists of standard HW Tests.
  """
  site_config.AddTemplate(
      'toolchain',
      # Make sure that we are doing a full build and that we are using AFDO.
      site_config.templates.full,
      site_config.templates.internal,
      site_config.templates.official_chrome,
      site_config.templates.no_vmtest_builder,
      build_type=constants.TOOLCHAIN_TYPE,
      buildslave_type=constants.GCE_BEEFY_BUILD_SLAVE_TYPE,
      images=['base', 'test', 'recovery'],
      build_timeout=(15 * 60 + 50) * 60,
      # Need to re-enable platform_SyncCrash after issue 658864 is fixed.
      useflags=append_useflags(['-cros-debug', '-test_security_OpenFDs',
                                '-test_platform_SyncCrash']),
      afdo_use=True,
      manifest=constants.OFFICIAL_MANIFEST,
      manifest_version=True,
      git_sync=False,
      trybot_list=False,
      description="Toolchain Builds (internal)",
  )
  site_config.AddTemplate(
      'gcc_toolchain',
      site_config.templates.toolchain,
      description='Full release build with next minor GCC toolchain revision',
      gcc_githash='svn-mirror/google/gcc-4_9',
      latest_toolchain=True,
      hw_tests=hw_test_list.ToolchainTestFull(),
      hw_tests_override=hw_test_list.ToolchainTestFull(),
  )
  site_config.AddTemplate(
      'llvm_toolchain',
      site_config.templates.toolchain,
      description='Full release build with LLVM toolchain',
      profile='llvm',
      hw_tests=hw_test_list.ToolchainTestMedium(constants.HWTEST_SUITES_POOL),
      hw_tests_override=hw_test_list.ToolchainTestMedium(
          constants.HWTEST_SUITES_POOL),
  )
  site_config.AddTemplate(
      'llvm_next_toolchain',
      site_config.templates.llvm_toolchain,
      description='Full release build with LLVM (next) toolchain',
      useflags=['clang', 'llvm-next', '-test_security_OpenFDs',
                '-test_platform_SyncCrash'],
  )

  ### Toolchain waterfall entries.
  ### Toolchain builder configs: 4 architectures {amd64,arm,x86,arm64}
  ###                          x 3 toolchains {gcc,llvm,llvm-next}
  ### All of these builders should be slaves of 'master-toolchain'.

  ### Master toolchain config.
  site_config.Add(
      'master-toolchain',
      site_config.templates.toolchain,
      boards=[],
      description='Toolchain master (all others are slaves).',
      master=True,
      sync_chrome=True,
      health_alert_recipients=['c-compiler-chrome@google.com'],
      health_threshold=1,
      afdo_use=False,
      important=True,
      active_waterfall=constants.WATERFALL_INTERNAL,
      buildslave_type=constants.GCE_WIMPY_BUILD_SLAVE_TYPE,
  )

  def toolchainSlaveHelper(name, board, *args, **kwargs):
    site_config.Add(
        name + '-gcc-toolchain',
        site_config.templates.gcc_toolchain,
        *args,
        boards=['peppy' if name == 'amd64' else board],
        important=True,
        active_waterfall=constants.WATERFALL_INTERNAL,
        **kwargs
    )

    if board == 'x86-alex':
      site_config.Add(
          name + '-llvm-toolchain',
          site_config.templates.llvm_toolchain,
          *args,
          boards=[board],
          important=True,
          active_waterfall=constants.WATERFALL_INTERNAL,
          **kwargs
      )
    else:
      site_config.Add(
          name + '-llvm-toolchain',
          site_config.templates.llvm_toolchain,
          *args,
          boards=[board],
          important=True,
          active_waterfall=constants.WATERFALL_INTERNAL,
          hw_tests=hw_test_list.ToolchainTestMedium(constants.HWTEST_MACH_POOL),
          hw_tests_override=hw_test_list.ToolchainTestMedium(
              constants.HWTEST_MACH_POOL),
          **kwargs
      )

    site_config.Add(
        name + '-llvm-next-toolchain',
        site_config.templates.llvm_next_toolchain,
        *args,
        boards=[board],
        important=True,
        active_waterfall=constants.WATERFALL_INTERNAL,
        **kwargs
    )

  # Create all waterfall slave builders.
  toolchainSlaveHelper('amd64', 'samus')
  toolchainSlaveHelper('x86', 'x86-alex',
                       hw_tests=hw_test_list.ToolchainTestLight(),
                       hw_tests_override=hw_test_list.ToolchainTestLight())
  toolchainSlaveHelper('arm', 'veyron_jaq')
  toolchainSlaveHelper('arm64', 'elm')

  #
  # Create toolchain tryjob builders.
  #
  toolchain_tryjob_boards = frozenset([
      'chell',
      'daisy',
      'link',
      'lulu',
      'nyan_big',
      'peach_pit',
      'peppy',
      'sentry',
      'squawks',
      'terra',
  ])
  site_config.AddForBoards(
      'gcc-toolchain',
      toolchain_tryjob_boards,
      board_configs,
      site_config.templates.gcc_toolchain,
  )
  site_config.AddForBoards(
      'llvm-toolchain',
      toolchain_tryjob_boards,
      board_configs,
      site_config.templates.llvm_toolchain,
  )
  site_config.AddForBoards(
      'llvm-next-toolchain',
      toolchain_tryjob_boards,
      board_configs,
      site_config.templates.llvm_next_toolchain,
  )

def PreCqBuilders(site_config, board_configs, hw_test_list):
  """Create all build configs associated with the PreCQ.

  Args:
    site_config: config_lib.SiteConfig to be modified by adding templates
                 and configs.
    board_configs: Dictionary mapping board names to per-board configurations.
    hw_test_list: Object to help create lists of standard HW Tests.
  """
  site_config.AddTemplate(
      'pre_cq',
      site_config.templates.paladin,
      active_waterfall=constants.WATERFALL_TRYBOT,
      build_type=constants.INCREMENTAL_TYPE,
      build_packages_in_background=True,
      pre_cq=True,
      archive=False,
      chrome_sdk=False,
      chroot_replace=True,
      debug_symbols=False,
      prebuilts=False,
      cpe_export=False,
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE)],
      vm_tests_override=None,
      description='Verifies compilation, building an image, and vm/unit tests '
                  'if supported.',
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Pre-CQ',
      health_alert_recipients=['chromeos-infra-eng@grotations.appspotmail.com'],
      health_threshold=3,
  )

  # Pre-CQ targets that only check compilation and unit tests.
  site_config.AddTemplate(
      'unittest_only_pre_cq',
      site_config.templates.pre_cq,
      site_config.templates.no_vmtest_builder,
      description='Verifies compilation and unit tests only',
      compilecheck=True,
  )

  # Pre-CQ targets that don't run VMTests.
  site_config.AddTemplate(
      'no_vmtest_pre_cq',
      site_config.templates.pre_cq,
      site_config.templates.no_vmtest_builder,
      description='Verifies compilation, building an image, and unit tests '
                  'if supported.',
  )

  # Pre-CQ targets that only check compilation.
  site_config.AddTemplate(
      'compile_only_pre_cq',
      site_config.templates.unittest_only_pre_cq,
      description='Verifies compilation only',
      unittests=False,
  )

  site_config.AddWithoutTemplate(
      'pre-cq-launcher',
      site_config.templates.paladin,
      site_config.templates.internal_paladin,
      site_config.templates.no_vmtest_builder,
      site_config.templates.no_hwtest_builder,
      boards=[],
      build_type=constants.PRE_CQ_LAUNCHER_TYPE,
      active_waterfall=constants.WATERFALL_INTERNAL,
      buildslave_type=constants.GCE_WIMPY_BUILD_SLAVE_TYPE,
      description='Launcher for Pre-CQ builders',
      trybot_list=False,
      manifest_version=False,
      # Every Pre-CQ launch failure should send out an alert.
      health_threshold=1,
      health_alert_recipients=['chromeos-infra-eng@grotations.appspotmail.com',
                               'tree'],
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Pre-CQ',
  )


  # Add a pre-cq config for every board.
  site_config.AddForBoards(
      'pre-cq',
      _all_boards,
      board_configs,
      site_config.templates.pre_cq,
  )
  site_config.AddForBoards(
      'no-vmtest-pre-cq',
      _all_boards,
      board_configs,
      site_config.templates.no_vmtest_pre_cq,
  )
  site_config.AddForBoards(
      'compile-only-pre-cq',
      _all_boards,
      board_configs,
      site_config.templates.compile_only_pre_cq,
  )
  site_config.Add(
      constants.BINHOST_PRE_CQ,
      site_config.templates.pre_cq,
      site_config.templates.no_vmtest_pre_cq,
      site_config.templates.internal,
      boards=[],
      binhost_test=True,
  )

  # TODO(davidjames): Add peach_pit, nyan, and beaglebone to pre-cq.
  # TODO(davidjames): Update daisy_spring to build images again.
  site_config.AddGroup(
      'mixed-a-pre-cq',
      # daisy_spring w/kernel 3.8.
      site_config['daisy_spring-compile-only-pre-cq'],
      # lumpy w/kernel 3.8.
      site_config['lumpy-compile-only-pre-cq'],
  )

  site_config.AddGroup(
      'mixed-b-pre-cq',
      # samus w/kernel 3.14.
      site_config['samus-compile-only-pre-cq'],
  )

  site_config.AddGroup(
      'mixed-c-pre-cq',
      # brillo
      site_config['storm-compile-only-pre-cq'],
  )

  site_config.AddGroup(
      'external-mixed-pre-cq',
      site_config['x86-generic-no-vmtest-pre-cq'],
      site_config['amd64-generic-no-vmtest-pre-cq'],
  )

  site_config.AddGroup(
      'kernel-3_14-a-pre-cq',
      site_config['x86-generic-no-vmtest-pre-cq'],
      site_config['arm-generic-no-vmtest-pre-cq']
  )

  site_config.AddGroup(
      'kernel-3_14-b-pre-cq',
      site_config['storm-no-vmtest-pre-cq'],
  )

  site_config.AddGroup(
      'kernel-3_14-c-pre-cq',
      site_config['veyron_pinky-no-vmtest-pre-cq'],
  )

  # Wifi specific PreCQ.
  site_config.AddTemplate(
      'wificell_pre_cq',
      site_config.templates.pre_cq,
      unittests=False,
      hw_tests=hw_test_list.WiFiCellPoolPreCQ(),
      hw_tests_override=hw_test_list.WiFiCellPoolPreCQ(),
      archive=True,
      image_test=False,
      description='WiFi tests acting as pre-cq for WiFi related changes',
  )

  site_config.AddGroup(
      'mixed-wificell-pre-cq',
      site_config.Add(
          'winky-wificell-pre-cq',
          site_config.templates.wificell_pre_cq,
          board_configs['winky']),
      site_config.Add(
          'veyron_speedy-wificell-pre-cq',
          site_config.templates.wificell_pre_cq,
          board_configs['veyron_speedy']),
      site_config.Add(
          'veyron_jerry-wificell-pre-cq',
          site_config.templates.wificell_pre_cq,
          board_configs['veyron_jerry']),
      site_config.Add(
          'daisy-wificell-pre-cq',
          site_config.templates.wificell_pre_cq,
          board_configs['daisy']),
  )


def AndroidPfqBuilders(site_config, board_configs, hw_test_list):
  """Create all build configs associated with the Android PFQ.

  Args:
    site_config: config_lib.SiteConfig to be modified by adding templates
                 and configs.
    board_configs: Dictionary mapping board names to per-board configurations.
    hw_test_list: Object to help create lists of standard HW Tests.
  """
  site_config.AddTemplate(
      'android_pfq',
      site_config.templates.default_hw_tests_override,
      build_type=constants.ANDROID_PFQ_TYPE,
      important=True,
      uprev=False,
      overlays=constants.BOTH_OVERLAYS,
      manifest_version=True,
      android_rev=constants.ANDROID_REV_LATEST,
      description='Preflight Android Uprev & Build (internal)',
      vm_tests=[config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE),
                config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE)],
      vm_tests_override=None,
  )

  site_config.Add(
      'master-android-pfq',
      site_config.templates.android_pfq,
      site_config.templates.internal,
      buildslave_type=constants.GCE_WIMPY_BUILD_SLAVE_TYPE,
      boards=[],
      master=True,
      push_overlays=constants.BOTH_OVERLAYS,
  )

  _android_pfq_hwtest_boards = frozenset([
      'cyan',
      'samus',
      'veyron_minnie',
  ])

  _android_pfq_no_hwtest_boards = frozenset([
      'glados-cheets',
  ])

  site_config.AddForBoards(
      'android-pfq',
      _android_pfq_hwtest_boards,
      board_configs,
      site_config.templates.android_pfq,
      hw_tests=hw_test_list.SharedPoolAndroidPFQ(),
  )
  site_config.AddForBoards(
      'android-pfq',
      _android_pfq_no_hwtest_boards,
      board_configs,
      site_config.templates.android_pfq,
  )


def _GetConfig(site_config, board_configs, hw_test_list):
  """Method with un-refactored build configs/templates.

  Args:
    site_config: config_lib.SiteConfig to be modified by adding templates
                 and configs.
    ge_build_config: Dictionary containing the decoded GE configuration file.
    board_configs: Dictionary mapping board names to per-board configurations.
    hw_test_list: Object to help create lists of standard HW Tests.
    is_release_branch: True if config for a release branch, False for TOT.
  """

  site_config.Add(
      'master-chromium-pfq',
      site_config.templates.chromium_pfq,
      boards=[],
      master=True,
      binhost_test=True,
      push_overlays=constants.BOTH_OVERLAYS,
      afdo_update_ebuild=True,
      chrome_sdk=False,
      health_alert_recipients=['chromeos-infra-eng@grotations.appspotmail.com',
                               'tree',
                               'chrome'],
  )

  _chromium_pfq_important_boards = frozenset([
      'arm-generic',
      'daisy',
      'veyron_jerry',
      'x86-generic',
      'amd64-generic',
  ])

  def _AddFullConfigs():
    """Add x86 and arm full configs."""
    external_overrides = config_lib.BuildConfig(
        manifest=constants.DEFAULT_MANIFEST,
        useflags=append_useflags(['-%s' % constants.USE_CHROME_INTERNAL]),
    )
    site_config.AddForBoards(
        config_lib.CONFIG_TYPE_FULL,
        _all_full_boards,
        board_configs,
        site_config.templates.full,
        internal=False,
        manifest_repo_url=site_config.params['MANIFEST_URL'],
        overlays=constants.PUBLIC_OVERLAYS,
        prebuilts=constants.PUBLIC,
        **external_overrides)
    site_config.AddForBoards(
        'tot-chromium-pfq-informational',
        _all_full_boards,
        board_configs,
        site_config.templates.chromium_pfq_informational,
        important=False,
        internal=False,
        manifest_repo_url=site_config.params['MANIFEST_URL'],
        overlays=constants.PUBLIC_OVERLAYS,
        **external_overrides)
    site_config.AddForBoards(
        'tot-chromium-pfq-informational-gn',
        _all_full_boards,
        board_configs,
        site_config.templates.chromium_pfq_informational_gn,
        important=False,
        internal=False,
        manifest_repo_url=site_config.params['MANIFEST_URL'],
        overlays=constants.PUBLIC_OVERLAYS,
        **external_overrides)
    # Create important configs, then non-important configs.
    site_config.AddForBoards(
        'chromium-pfq',
        _chromium_pfq_important_boards,
        board_configs,
        site_config.templates.chromium_pfq,
        **external_overrides)
    site_config.AddForBoards(
        'chromium-pfq',
        _all_full_boards - _chromium_pfq_important_boards,
        board_configs,
        site_config.templates.chromium_pfq,
        important=False,
        **external_overrides)

  _AddFullConfigs()

  _chrome_pfq_important_boards = frozenset([
      'cyan',
      'daisy_skate',
      'falco',
      'lumpy',
      'nyan',
      'peach_pit',
      'peppy',
      'tricky',
      'veyron_minnie',
      'veyron_pinky',
      'veyron_rialto',
      'x86-alex',
  ])

  _telemetry_boards = frozenset([
      'amd64-generic',
      'arm-generic',
      'x86-generic',
  ])

  site_config.AddForBoards(
      'telemetry',
      _telemetry_boards,
      board_configs,
      site_config.templates.telemetry,
  )

  site_config.Add(
      'x86-generic-asan',
      site_config.templates.asan,
      site_config.templates.incremental,
      boards=['x86-generic'],
      chroot_replace=True,
      hw_tests=hw_test_list.AsanTest(),
      hw_tests_override=hw_test_list.AsanTest(),
      description='Build with Address Sanitizer (Clang)',
      trybot_list=True,
  )

  site_config.Add(
      'x86-generic-tot-asan-informational',
      site_config.templates.tot_asan_informational,
      boards=['x86-generic'],
  )

  site_config.Add(
      'amd64-generic-asan',
      site_config.templates.asan,
      site_config.templates.incremental,
      boards=['amd64-generic'],
      description='Build with Address Sanitizer (Clang)',
      trybot_list=True,
  )

  site_config.Add(
      'amd64-generic-tot-asan-informational',
      site_config.templates.tot_asan_informational,
      boards=['amd64-generic'],
  )

  site_config.Add(
      'beaglebone-incremental',
      site_config.templates.incremental,
      site_config.templates.beaglebone,
      boards=['beaglebone'],
      trybot_list=True,
      description='Incremental Beaglebone Builder',
  )

  site_config.Add(
      'x86-generic-incremental',
      site_config.templates.incremental,
      board_configs['x86-generic'],
  )

  # Build external source, for an internal board.
  site_config.Add(
      'daisy-incremental',
      site_config.templates.incremental,
      board_configs['daisy'],
      site_config.templates.external,
      useflags=append_useflags(['-chrome_internal']),
  )

  site_config.Add(
      'amd64-generic-incremental',
      site_config.templates.incremental,
      board_configs['amd64-generic'],
      # This builder runs on a VM, so it can't run VM tests.
      vm_tests=[],
  )

  site_config.Add(
      'x32-generic-incremental',
      site_config.templates.incremental,
      board_configs['x32-generic'],
      # This builder runs on a VM, so it can't run VM tests.
      vm_tests=[],
  )

  site_config.Add(
      'x86-generic-asan-paladin',
      site_config.templates.paladin,
      board_configs['x86-generic'],
      site_config.templates.asan,
      description='Paladin build with Address Sanitizer (Clang)',
      important=False,
  )

  site_config.Add(
      'amd64-generic-asan-paladin',
      site_config.templates.paladin,
      board_configs['amd64-generic'],
      site_config.templates.asan,
      description='Paladin build with Address Sanitizer (Clang)',
      important=False,
  )

  _chrome_perf_boards = frozenset([
      'daisy',
      'lumpy',
      'parrot',
  ])

  site_config.AddForBoards(
      'chrome-perf',
      _chrome_perf_boards,
      board_configs,
      site_config.templates.chrome_perf,
      trybot_list=True,
  )

  site_config.AddForBoards(
      'telem-chromium-pfq-informational',
      ['x86-generic', 'amd64-generic'],
      board_configs,
      site_config.templates.chromium_pfq_informational,
      site_config.templates.telemetry,
      site_config.templates.chrome_try,
  )

  #
  # Internal Builds
  #
  site_config.AddForBoards(
      'nowithdebug-paladin',
      ['x86-generic', 'amd64-generic'],
      board_configs,
      site_config.templates.paladin,
      site_config.templates.internal_nowithdebug_paladin,
      important=False,
  )

  site_config.Add(
      'x86-mario-nowithdebug-paladin',
      site_config.templates.paladin,
      site_config.templates.internal_nowithdebug_paladin,
      boards=['x86-mario'])

  # Used for builders which build completely from source except Chrome.

  # These boards pass with -clang-clean CFLAG, so ensure they stay that way.
  site_config.AddForBoards(
      'full-compile-paladin',
      ['falco', 'nyan'],
      board_configs,
      site_config.templates.paladin,
      board_replace=True,
      chrome_binhost_only=True,
      chrome_sdk=False,
      compilecheck=True,
      cpe_export=False,
      debug_symbols=False,
      prebuilts=False,
      unittests=False,
      upload_hw_test_artifacts=False,
      vm_tests=[],
  )

  site_config.Add(
      'samus-pre-flight-branch',
      site_config.templates.pre_flight_branch,
      master=True,
      push_overlays=constants.BOTH_OVERLAYS,
      boards=['samus'],
      android_rev=constants.ANDROID_REV_LATEST,
      afdo_generate=True,
      afdo_update_ebuild=True,
      vm_tests=[],
      hw_tests=[hw_test_list.AFDORecordTest()],
  )

  # Create our unittest stress build configs (used for tryjobs only)
  site_config.AddForBoards(
      'unittest-stress',
      _all_boards,
      board_configs,
      site_config.templates.unittest_stress,
  )

  ### Master paladin (CQ builder).

  site_config.Add(
      'master-paladin',
      site_config.templates.paladin,
      site_config.templates.internal_paladin,
      boards=[],
      buildslave_type=constants.BAREMETAL_BUILD_SLAVE_TYPE,
      master=True,
      binhost_test=True,
      push_overlays=constants.BOTH_OVERLAYS,
      description='Commit Queue master (all others are slaves)',

      # This name should remain synced with with the name used in
      # build_internals/masters/master.chromeos/board_config.py.
      # TODO(mtennant): Fix this.  There should be some amount of auto-
      # configuration in the board_config.py code.
      health_threshold=3,
      health_alert_recipients=['chromeos-infra-eng@grotations.appspotmail.com',
                               'tree'],
      sanity_check_slaves=['wolf-tot-paladin'],
      trybot_list=False,
      auto_reboot=False,
  )

  ### Other paladins (CQ builders).
  # These are slaves of the master paladin by virtue of matching
  # in a few config values (e.g. 'build_type', 'branch', etc).  If
  # they are not 'important' then they are ignored slaves.
  # TODO(mtennant): This master-slave relationship should be specified
  # here in the configuration, rather than GetSlavesForMaster().
  # Something like the following:
  # master_paladin = site_config.AddConfig(internal_paladin, ...)
  # master_paladin.AddSlave(site_config.AddConfig(internal_paladin, ...))

  # Sanity check builder, part of the CQ but builds without the patches
  # under test.
  site_config.Add(
      'wolf-tot-paladin',
      site_config.templates.paladin,
      site_config.templates.internal_paladin,
      boards=['wolf'],
      do_not_apply_cq_patches=True,
      prebuilts=False,
      hw_tests=hw_test_list.SharedPoolCQ(),
  )

  _paladin_boards = _all_boards

  # List of paladin boards where the regular paladin config is important.
  _paladin_important_boards = frozenset([
      'amd64-generic',
      'arm-generic',
      'auron',
      'beaglebone',
      'butterfly',
      'cyan',
      'daisy',
      'daisy_skate',
      'daisy_spring',
      'elm',
      'falco',
      'glados',
      'guado_moblab',
      'gru',
      'lakitu',
      'leon',
      'link',
      'lumpy',
      'monroe',
      'nyan',
      'oak',
      'panther',
      'parrot',
      'peach_pit',
      'peppy',
      'rambi',
      'samus',
      'smaug',
      'storm',
      'stout',
      'strago',
      'stumpy',
      'tricky',
      'veyron_mighty',
      'veyron_minnie',
      'veyron_pinky',
      'veyron_rialto',
      'veyron_speedy',
      'whirlwind',
      'wolf',
      'x86-alex',
      'x86-generic',
      'x86-mario',
      'x86-zgb',
  ])

  _paladin_simple_vmtest_boards = frozenset([
      'rambi',
      'x86-mario',
  ])

  _paladin_devmode_vmtest_boards = frozenset([
      'parrot',
  ])

  _paladin_cros_vmtest_boards = frozenset([
      'stout',
  ])

  _paladin_smoke_vmtest_boards = frozenset([
      'amd64-generic',
      'x86-generic',
  ])

  _paladin_default_vmtest_boards = frozenset([
      'x32-generic',
  ])

  _paladin_hwtest_boards = frozenset([
      'daisy_skate',
      'elm',
      'link',
      'lumpy',
      'peach_pit',
      'peppy',
      'stumpy',
      'veyron_mighty',
      'veyron_speedy',
      'wolf',
      'x86-alex',
      'x86-zgb',
  ])


  # Jetstream devices run unique hw tests
  _paladin_jetstream_hwtest_boards = frozenset([
      'whirlwind',
  ])

  _paladin_moblab_hwtest_boards = frozenset([
      'guado_moblab',
  ])

  # *-cheets devices run a different suite
  _paladin_cheets_hwtest_boards = frozenset([
      'cyan',
      'veyron_minnie',
  ])

  _paladin_chroot_replace_boards = frozenset([
      'butterfly',
      'daisy_spring',
  ])

  _paladin_separate_symbols = frozenset([
      'amd64-generic',
  ])

  def _CreatePaladinConfigs():
    for board in _paladin_boards:
      assert board in board_configs, '%s not in board_configs' % board
      config_name = '%s-%s' % (board, constants.PALADIN_TYPE)
      customizations = config_lib.BuildConfig()
      base_config = board_configs[board]
      if board in _paladin_hwtest_boards:
        customizations.update(hw_tests=hw_test_list.DefaultListCQ())
      if board in _paladin_moblab_hwtest_boards:
        customizations.update(
            hw_tests=[
                config_lib.HWTestConfig(
                    constants.HWTEST_MOBLAB_QUICK_SUITE,
                    num=1, timeout=120*60,
                    pool=constants.HWTEST_PALADIN_POOL)
            ])
      if board in _paladin_cheets_hwtest_boards:
        customizations.update(
            hw_tests=[
                config_lib.HWTestConfig(
                    constants.HWTEST_ARC_COMMIT_SUITE,
                    pool=constants.HWTEST_PALADIN_POOL)
            ])
      if board in _paladin_jetstream_hwtest_boards:
        customizations.update(
            hw_tests=[
                config_lib.HWTestConfig(
                    constants.HWTEST_JETSTREAM_COMMIT_SUITE,
                    pool=constants.HWTEST_PALADIN_POOL)
            ])
      if board not in _paladin_important_boards:
        customizations.update(important=False)
      if board in _paladin_chroot_replace_boards:
        customizations.update(chroot_replace=True)
      if board in _internal_boards:
        customizations = customizations.derive(
            site_config.templates.internal,
            site_config.templates.official_chrome,
            manifest=constants.OFFICIAL_MANIFEST)
      if board in _paladin_separate_symbols:
        customizations.update(separate_debug_symbols=True)

      if board not in _paladin_default_vmtest_boards:
        vm_tests = []
        if board in _paladin_simple_vmtest_boards:
          vm_tests.append(
              config_lib.VMTestConfig(constants.SIMPLE_AU_TEST_TYPE))
        if board in _paladin_cros_vmtest_boards:
          vm_tests.append(config_lib.VMTestConfig(constants.CROS_VM_TEST_TYPE))
        if board in _paladin_devmode_vmtest_boards:
          vm_tests.append(config_lib.VMTestConfig(constants.DEV_MODE_TEST_TYPE))
        if board in _paladin_smoke_vmtest_boards:
          vm_tests.append(
              config_lib.VMTestConfig(constants.SMOKE_SUITE_TEST_TYPE))

        customizations.update(vm_tests=vm_tests)

        if site_config.templates.paladin.vm_tests_override is not None:
          # Make sure any new tests are also in override.
          override = site_config.templates.paladin.vm_tests_override[:]
          for test in vm_tests:
            if test not in override:
              override.append(test)

          customizations.update(vm_tests_override=override)

      if base_config.get('internal'):
        customizations.update(
            prebuilts=constants.PRIVATE,
            description=(site_config.templates.paladin.description +
                         ' (internal)'))
      else:
        customizations.update(prebuilts=constants.PUBLIC)

      if board in _lakitu_boards:
        # Note: Because |customizations| precedes |base_config|, it will be
        # overridden by |base_config|. So we have to append lakitu
        # customizations after |base_config| is applied.
        # TODO(crbug.com/553749)
        # Also, I can't do
        # `lakitu_test_customizations if xxx else None` because the Add function
        # doesn't allow optional args to be None.
        site_config.Add(
            config_name,
            site_config.templates.paladin,
            customizations,
            base_config,
            site_config.templates.lakitu_test_customizations,
        )
      else:
        site_config.Add(
            config_name,
            site_config.templates.paladin,
            customizations,
            base_config,
        )

  _CreatePaladinConfigs()

  site_config.Add(
      'lumpy-incremental-paladin',
      site_config.templates.paladin,
      site_config.templates.internal_paladin,
      boards=['lumpy'],
      build_before_patching=True,
      prebuilts=False,
      compilecheck=True,
      unittests=False,
  )

  def ShardHWTestsBetweenBuilders(*args):
    """Divide up the hardware tests between the given list of config names.

    Each of the config names must have the same hardware test suites, and the
    number of suites must be equal to the number of config names.

    Args:
      *args: A list of config names.
    """
    # List of config names.
    names = args
    # Verify sanity before sharding the HWTests.
    for name in names:
      if name is not None:
        assert len(site_config[name].hw_tests) == len(names), \
          '%s should have %d tests, but found %d' % (
              name, len(names), len(site_config[name].hw_tests))
    active_names = [name for name in names if name is not None]
    if len(active_names) > 1:
      for name in active_names[1:]:
        for test1, test2 in zip(site_config[name].hw_tests,
                                site_config[active_names[0]].hw_tests):
          assert test1.__dict__ == test2.__dict__, \
            '%s and %s have different hw_tests configured' % (
                active_names[0], name)
    # Assign each config the Nth HWTest.
    for i, name in enumerate(names):
      if name is not None:
        site_config[name]['hw_tests'] = [site_config[name].hw_tests[i]]

  # Shard the bvt-inline and bvt-cq hw tests between similar builders.
  # The first builder gets bvt-inline, and the second builder gets bvt-cq.
  # bvt-cq takes longer, so it usually makes sense to give it the faster board.
  ShardHWTestsBetweenBuilders('x86-zgb-paladin', 'x86-alex-paladin')
  ShardHWTestsBetweenBuilders('wolf-paladin', 'peppy-paladin')
  ShardHWTestsBetweenBuilders('daisy_skate-paladin', 'peach_pit-paladin')
  ShardHWTestsBetweenBuilders('veyron_mighty-paladin', 'veyron_speedy-paladin')
  ShardHWTestsBetweenBuilders('lumpy-paladin', 'stumpy-paladin')
  ShardHWTestsBetweenBuilders('elm-paladin', None)

  site_config.Add(
      'mario-incremental',
      site_config.templates.incremental,
      site_config.templates.internal_incremental,
      boards=['x86-mario'],
  )

  site_config.Add(
      'lakitu-incremental',
      site_config.templates.incremental,
      site_config.templates.internal_incremental,
      board_configs['lakitu'],
      site_config.templates.lakitu_test_customizations,
  )

  site_config.Add(
      'lakitu_next-incremental',
      site_config.templates.incremental,
      site_config.templates.internal_incremental,
      board_configs['lakitu_next'],
      site_config.templates.lakitu_test_customizations,
  )

  # Now generate generic release-afdo configs if we haven't created anything
  # more specific above already. release-afdo configs are builders that do AFDO
  # profile collection and optimization in the same builder. Used by developers
  # that want to measure performance changes caused by their changes.
  def _AddAFDOConfigs():
    for board in _all_release_boards:
      base = board_configs[board]

      config_name = '%s-%s' % (board, config_lib.CONFIG_TYPE_RELEASE_AFDO)
      if config_name in site_config:
        continue

      generate_config_name = (
          '%s-%s-%s' % (board,
                        config_lib.CONFIG_TYPE_RELEASE_AFDO,
                        'generate'))
      use_config_name = '%s-%s-%s' % (board,
                                      config_lib.CONFIG_TYPE_RELEASE_AFDO,
                                      'use')

      # We can't use AFDO data if afdo_use is disabled for this board.
      if not base.get('afdo_use', True):
        continue

      site_config.AddGroup(
          config_name,
          site_config.Add(
              generate_config_name,
              site_config.templates.release_afdo_generate,
              base
          ),
          site_config.Add(
              use_config_name,
              site_config.templates.release_afdo_use,
              base
          ),
      )

  _AddAFDOConfigs()

  ### Informational hwtest

  _chrome_informational_hwtest_boards = frozenset([
      'peach_pit',
      'tricky',
  ])


  # We have to mark all autogenerated PFQs as not important so the master
  # does not wait for them.  http://crbug.com/386214
  # If you want an important PFQ, you'll have to declare it yourself.

  def _AddInformationalConfigs():
    site_config.AddForBoards(
        'tot-chrome-pfq-informational',
        _chrome_informational_hwtest_boards,
        board_configs,
        site_config.templates.chrome_pfq_informational,
        important=False,
        hw_tests=hw_test_list.DefaultListPFQ(
            pool=constants.HWTEST_CONTINUOUS_POOL),
    )
    informational_boards = set(_all_release_boards) - set(_cheets_boards)
    site_config.AddForBoards(
        'tot-chrome-pfq-informational',
        informational_boards-_chrome_informational_hwtest_boards,
        board_configs,
        site_config.templates.chrome_pfq_informational,
        important=False)
    site_config.AddForBoards(
        'tot-chrome-pfq-informational-gn',
        informational_boards,
        board_configs,
        site_config.templates.chrome_pfq_informational_gn,
        important=False)
    site_config.AddForBoards(
        'tot-chrome-pfq-cheets-informational',
        _cheets_boards,
        board_configs,
        site_config.templates.chrome_pfq_cheets_informational,
        important=False)

  def _AddPfqConfigs():
    site_config.AddForBoards(
        'chrome-pfq',
        _chrome_pfq_important_boards,
        board_configs,
        site_config.templates.chrome_pfq,
        important=True
    )
    site_config.AddForBoards(
        'chrome-pfq',
        _all_release_boards - _chrome_pfq_important_boards,
        board_configs,
        site_config.templates.chrome_pfq,
        important=False,
    )

  _AddInformationalConfigs()
  _AddPfqConfigs()

  _firmware_boards = frozenset([
      'asuka',
      'auron',
      'banjo',
      'banon',
      'butterfly',
      'candy',
      'caroline',
      'cave',
      'chell',
      'clapper',
      'cyan',
      'daisy',
      'daisy_skate',
      'daisy_spring',
      'edgar',
      'enguarde',
      'expresso',
      'falco',
      'gale',
      'glimmer',
      'gnawty',
      'gru',
      'jecht',
      'kefka',
      'kevin',
      'kip',
      'lars',
      'leon',
      'link',
      'lumpy',
      'monroe',
      'ninja',
      'orco',
      'panther',
      'parrot',
      'pbody',
      'peach_pi',
      'peach_pit',
      'peppy',
      'quawks',
      'rambi',
      'reks',
      'relm',
      'rikku',
      'samus',
      'sentry',
      'setzer',
      'slippy',
      'smaug',
      'squawks',
      'storm',
      'stout',
      'strago',
      'stumpy',
      'sumo',
      'swanky',
      'terra',
      'winky',
      'wolf',
      'x86-mario',
      'zako',
  ])

  _x86_depthcharge_firmware_boards = frozenset([
      'auron',
      'banjo',
      'candy',
      'clapper',
      'cyan',
      'enguarde',
      'expresso',
      'glados',
      'glimmer',
      'gnawty',
      'heli',
      'jecht',
      'kip',
      'kunimitsu',
      'leon',
      'link',
      'ninja',
      'orco',
      'quawks',
      'rambi',
      'rikku',
      'samus',
      'squawks',
      'strago',
      'sumo',
      'swanky',
      'winky',
      'zako',
  ])


  def _AddFirmwareConfigs():
    """Add x86 and arm firmware configs."""
    for board in _firmware_boards:
      site_config.Add(
          '%s-%s' % (board, config_lib.CONFIG_TYPE_FIRMWARE),
          site_config.templates.firmware,
          board_configs[board],
          site_config.templates.no_vmtest_builder,
      )

    for board in _x86_depthcharge_firmware_boards:
      site_config.Add(
          '%s-%s-%s' % (board, 'depthcharge', config_lib.CONFIG_TYPE_FIRMWARE),
          site_config.templates.depthcharge_firmware,
          board_configs[board],
          site_config.templates.no_vmtest_builder,
      )
      site_config.Add(
          '%s-%s-%s-%s' % (board, 'depthcharge', config_lib.CONFIG_TYPE_FULL,
                           config_lib.CONFIG_TYPE_FIRMWARE),
          site_config.templates.depthcharge_full_firmware,
          board_configs[board],
          site_config.templates.no_vmtest_builder,
      )

  _AddFirmwareConfigs()

  # This is an example factory branch configuration.
  # Modify it to match your factory branch.
  site_config.Add(
      'x86-mario-factory',
      site_config.templates.factory,
      boards=['x86-mario'],
  )


def ReleaseBuilders(site_config, board_configs, ge_build_config,
                    is_release_branch):
  """Create all release builders.

  Args:
    site_config: config_lib.SiteConfig to be modified by adding templates
                 and configs.
    board_configs: Dictionary mapping board names to per-board configurations.
    ge_build_config: Dictionary containing the decoded GE configuration file.
    is_release_branch: True if config for a release branch, False for TOT.
  """
  ### Master release config.
  site_config.Add(
      'master-release',
      site_config.templates.release,
      boards=[],
      buildslave_type=constants.GCE_WIMPY_BUILD_SLAVE_TYPE,
      master=True,
      sync_chrome=False,
      chrome_sdk=False,
      health_alert_recipients=['chromeos-infra-eng@grotations.appspotmail.com',
                               'tree'],
      afdo_use=False,
      branch_util_test=True,
  )

  ### Release configs.

  _critical_for_chrome_boards = frozenset([
      'daisy',
      'lumpy',
      'parrot',
  ])

  site_config.AddForBoards(
      config_lib.CONFIG_TYPE_RELEASE,
      _critical_for_chrome_boards,
      board_configs,
      site_config.templates.release,
      critical_for_chrome=True,
  )

  builder_to_boards_dict = config_lib.GroupBoardsByBuilder(
      ge_build_config[config_lib.CONFIG_TEMPLATE_BOARDS])

  _all_release_builder_boards = builder_to_boards_dict[
      config_lib.CONFIG_TEMPLATE_RELEASE]

  site_config.AddForBoards(
      config_lib.CONFIG_TYPE_RELEASE,
      ((_all_release_boards | _all_release_builder_boards) -
       _critical_for_chrome_boards),
      board_configs,
      site_config.templates.release,
  )


  def GetReleaseConfigName(board):
    """Convert a board name into a release config name."""
    return '%s-release' % board

  def GetConfigName(builder, board):
    """Convert a board name into a config name."""
    if builder == config_lib.CONFIG_TEMPLATE_RELEASE:
      return GetReleaseConfigName(board)
    else:
      # Currently just support RELEASE builders
      raise Exception('Do not support other builders.')

  def _GetConfigValues(builder, board):
    """Get and return config values from template"""

    def _GetConfigWaterfall(builder):
      if builder == config_lib.CONFIG_TEMPLATE_RELEASE:
        if is_release_branch:
          return constants.WATERFALL_RELEASE
        else:
          return constants.WATERFALL_INTERNAL
      else:
        # Currently just support RELEASE builders
        raise ValueError('Do not support builder %s.' % builder)

    config_values = {
        'important': not board[config_lib.CONFIG_TEMPLATE_EXPERIMENTAL],
        'active_waterfall': _GetConfigWaterfall(builder)
    }

    return config_values

  def _AdjustUngroupedReleaseConfigs(builder_ungrouped_dict):
    """Adjust for ungrouped release boards"""
    for builder in builder_ungrouped_dict:
      for board in builder_ungrouped_dict[builder]:
        config_name = GetConfigName(builder,
                                    board[config_lib.CONFIG_TEMPLATE_NAME])
        site_config[config_name].apply(
            _GetConfigValues(builder, board),
        )

  def _AdjustGroupedReleaseConfigs(builder_group_dict):
    """Adjust leader and follower configs for grouped boards"""
    for builder in builder_group_dict:
      for group in builder_group_dict[builder]:
        board_group = builder_group_dict[builder][group]

        # Leaders are built on baremetal builders and run all tests needed by
        # the related boards.
        for board in board_group.leader_boards:
          config_name = GetConfigName(builder,
                                      board[config_lib.CONFIG_TEMPLATE_NAME])
          site_config[config_name].apply(
              _GetConfigValues(builder, board),
          )

        # Followers are built on GCE instances, and turn off testing that breaks
        # on GCE. The missing tests run on the leader board.
        for board in board_group.follower_boards:
          config_name = GetConfigName(builder,
                                      board[config_lib.CONFIG_TEMPLATE_NAME])
          site_config[config_name].apply(
              _GetConfigValues(builder, board),
              buildslave_type=constants.GCE_BEEFY_BUILD_SLAVE_TYPE,
              chrome_sdk_build_chrome=False,
              vm_tests=[],
          )

  def _AdjustReleaseConfigs():
    """Adjust ungrouped and grouped release configs"""
    (builder_group_dict, builder_ungrouped_dict) = (
        config_lib.GroupBoardsByBuilderAndBoardGroup(
            ge_build_config[config_lib.CONFIG_TEMPLATE_BOARDS]))
    _AdjustUngroupedReleaseConfigs(builder_ungrouped_dict)
    _AdjustGroupedReleaseConfigs(builder_group_dict)

    for board in _cheets_boards:
      config_name = GetReleaseConfigName(board)
      # For boards in _cheets_boards, use cheets_release template
      site_config[config_name].apply(
          site_config.templates.cheets_release,
          board_configs[board],
      )

    for board in _moblab_boards:
      config_name = GetReleaseConfigName(board)
      # If the board is in _moblab_boards, use moblab_release template
      site_config[config_name].apply(
          site_config.templates.moblab_release,
          board_configs[board],
      )

  _AdjustReleaseConfigs()


def AddPayloadTryjobs(site_config):
  """Create <board>-payloads configs for all payload generating boards.

  We create a config named 'board-payloads' for every board which has a
  config with 'paygen' True. The idea is that we have a build that generates
  payloads, we need to have a tryjob to re-attempt them on failure.
  """
  for board in _all_release_boards:
    if site_config['%s-release' % board].paygen:
      site_config.Add(
          '%s-payloads' % board,
          site_config.templates.payloads,
          boards=[board],
      )


def InsertHwTestsOverrideDefaults(build, hw_test_list):
  """Insert default hw_tests values for a given build.

  Also updates child builds.

  Args:
    build: BuildConfig instance to modify in place.
    hw_test_list: Object to help create lists of standard HW Tests.
  """
  for child in build['child_configs']:
    InsertHwTestsOverrideDefaults(child, hw_test_list)

  if build['hw_tests_override'] is not None:
    # Explicitly set, no need to insert defaults.
    return

  if not build['hw_tests']:
    build['hw_tests_override'] = hw_test_list.DefaultList(
        num=constants.HWTEST_TRYBOT_NUM, pool=constants.HWTEST_TRYBOT_POOL,
        file_bugs=False)
  else:
    # Copy over base tests.
    build['hw_tests_override'] = [copy.copy(x) for x in build['hw_tests']]

    # Adjust for manual test environment.
    for hw_config in build['hw_tests_override']:
      hw_config.num = constants.HWTEST_TRYBOT_NUM
      hw_config.pool = constants.HWTEST_TRYBOT_POOL
      hw_config.file_bugs = False
      hw_config.priority = constants.HWTEST_DEFAULT_PRIORITY

  # TODO: Fix full_release_test.py/AUTest on trybots, crbug.com/390828.
  build['hw_tests_override'] = [
      hw_config for hw_config in build['hw_tests_override']
      if hw_config.suite != constants.HWTEST_AU_SUITE]


def InsertWaterfallDefaults(site_config, is_release_branch):
  """Method with un-refactored build configs/templates.

  Args:
    site_config: config_lib.SiteConfig containing builds to have their
                 waterfall values updated.
    is_release_branch: True if config for a release branch, False for TOT.
  """
  for name, c in site_config.iteritems():
    if not c.get('active_waterfall'):
      c['active_waterfall'] = GetDefaultWaterfall(c, is_release_branch)

  # Apply manual configs, not used for release branches.
  if not is_release_branch:
    for waterfall, names in _waterfall_config_map.iteritems():
      for name in names:
        site_config[name]['active_waterfall'] = waterfall


def ApplyCustomOverrides(site_config, hw_test_list):
  """Method with to override specific flags for specific builders.

  Generally try really hard to avoid putting anything here that isn't
  a really special case for a single specific builder. This is performed
  after every other bit of processing, so it always has the final say.

  Args:
    site_config: config_lib.SiteConfig containing builds to have their
                 waterfall values updated.
    hw_test_list: Object to help create lists of standard HW Tests.
  """
  overwritten_configs = {
      'amd64-generic-chromium-pfq': {
          'disk_layout': '2gb-rootfs',
          'useflags': [],
      },

      'beaglebone-paladin': {
          'chrome_sdk': False,
          'image_test': False,
          'rootfs_verification': False,
          'sync_chrome': False,
      },

      'beaglebone_servo-paladin': {
          'chrome_sdk': False,
          'image_test': False,
          'rootfs_verification': False,
          'sync_chrome': False,
      },

      'lakitu-pre-cq':
          site_config.templates.lakitu_test_customizations,

      'lakitu_next-pre-cq':
          site_config.templates.lakitu_test_customizations,

      ### Arm release configs
      'smaug-release' : {
          'paygen': False,
          'sign_types':['nv_lp0_firmware'],
      },

      # Move beaglebone-release to GCE until we're smart enough to put
      # everything there that doesn't use VM Tests.
      'beaglebone-release': {
          'buildslave_type': constants.GCE_BEEFY_BUILD_SLAVE_TYPE,
      },

      'beaglebone_servo-release': {
          'buildslave_type': constants.GCE_BEEFY_BUILD_SLAVE_TYPE,
      },

      # Hw Lab can't test storm, yet.
      'storm-release': {
          'paygen_skip_testing':True,
          'signer_tests':False,
      },

      'whirlwind-release': {
          'dev_installer_prebuilts':True,
      },

      'lakitu-release': config_lib.BuildConfig().apply(
          site_config.templates.lakitu_test_customizations,
          sign_types=['base'],
      ),

      'lakitu_next-release': config_lib.BuildConfig().apply(
          site_config.templates.lakitu_test_customizations,
          signer_tests=False,
      ),

      'guado_labstation-release': {
          'hw_tests': [
              config_lib.HWTestConfig(constants.HWTEST_CANARY_SUITE,
                                      num=1, timeout=120*60, warn_only=True,
                                      async=True, retry=False, max_retries=None,
                                      file_bugs=False),
          ],
          'image_test':False,
          'images':['test'],
          'signer_tests':False,
          'paygen':False,
          'vm_tests':[],
      },

      'lumpy-chrome-pfq': {
          'afdo_generate': True,
          # Disable hugepages before collecting AFDO profile.
          'useflags': append_useflags(['-transparent_hugepage']),
          'hw_tests': ([hw_test_list.AFDORecordTest()] +
                       hw_test_list.SharedPoolPFQ()),
      },

      'cyan-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolAndroidPFQ(),
      },

      'daisy_skate-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolPFQ(),
      },

      'falco-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolPFQ(),
      },

      'veyron_minnie-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolAndroidPFQ(),
      },

      'peach_pit-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolPFQ(),
      },

      'tricky-chrome-pfq': {
          'hw_tests': hw_test_list.SharedPoolPFQ(),
      },
  }

  for config_name, overrides  in overwritten_configs.iteritems():
    # TODO: Turn this assert into a unittest.
    # config = site_config[config_name]
    # for k, v in overrides.iteritems():
    #   assert config[k] != v, ('Unnecessary override: %s: %s' %
    #                           (config_name, k))
    site_config[config_name].apply(**overrides)


def SpecialtyBuilders(site_config):
  """Add a variety of specialized builders or tryjobs."""
  site_config.AddWithoutTemplate(
      'chromiumos-sdk',
      site_config.templates.full,
      site_config.templates.no_hwtest_builder,
      # The amd64-host has to be last as that is when the toolchains
      # are bundled up for inclusion in the sdk.
      boards=[
          'x86-generic', 'arm-generic', 'amd64-generic', 'veyron_jaq'
      ],
      build_type=constants.CHROOT_BUILDER_TYPE,
      active_waterfall=constants.WATERFALL_EXTERNAL,
      buildslave_type=constants.BAREMETAL_BUILD_SLAVE_TYPE,
      builder_class_name='sdk_builders.ChrootSdkBuilder',
      use_sdk=False,
      trybot_list=True,
      prebuilts=constants.PUBLIC,
      description='Build the SDK and all the cross-compilers',
      doc='http://www.chromium.org/chromium-os/build/builder-overview#'
          'TOC-Continuous',
  )

  site_config.AddWithoutTemplate(
      'config-updater',
      site_config.templates.no_hwtest_builder,
      important=True,
      vm_tests=[],
      description=('Build Config Updater reads updated GE config files from'
                   ' GS, and commits them to chromite after running tests.'),
      build_type=constants.CONFIG_UPDATER_TYPE,
      boards=[],
      builder_class_name='config_builders.UpdateConfigBuilder',
      active_waterfall=constants.WATERFALL_INFRA,
      buildslave_type=constants.GCE_WIMPY_BUILD_SLAVE_TYPE,
  )

  site_config.AddWithoutTemplate(
      constants.BRANCH_UTIL_CONFIG,
      site_config.templates.paladin,
      site_config.templates.internal_paladin,
      site_config.templates.no_vmtest_builder,
      site_config.templates.no_hwtest_builder,
      boards=[],
      # Disable postsync_patch to prevent conflicting patches from being applied
      # - e.g., patches from 'master' branch being applied to a branch.
      postsync_patch=False,
      # Disable postsync_reexec to continue running the 'master' branch chromite
      # for all stages, rather than the chromite in the branch buildroot.
      postsync_reexec=False,
      # Need to reset the paladin build_type we inherited.
      build_type=None,
      builder_class_name='release_builders.CreateBranchBuilder',
      description='Used for creating/deleting branches (TPMs only)',
      active_waterfall=constants.WATERFALL_TRYBOT,
  )

  site_config.AddWithoutTemplate(
      'sync-test-cbuildbot',
      site_config.templates.no_hwtest_builder,
      boards=[],
      build_type=constants.INCREMENTAL_TYPE,
      builder_class_name='test_builders.ManifestVersionedSyncBuilder',
      chroot_replace=True,
      description='Sync tryjob to help with cbuildbot development',
      active_waterfall=constants.WATERFALL_TRYBOT,
  )

  site_config.AddGroup(
      'test-ap-group',
      site_config.Add('stumpy-test-ap',
                      site_config.templates.test_ap,
                      boards=['stumpy']),
      site_config.Add('panther-test-ap',
                      site_config.templates.test_ap,
                      boards=['panther']),
      site_config.Add('whirlwind-test-ap',
                      site_config.templates.test_ap,
                      boards=['whirlwind']),
      description='Create images used to power access points in WiFi lab.',
  )


@factory.CachedFunctionCall
def GetConfig():
  """Create the Site configuration for all ChromeOS builds.

  Returns:
    A config_lib.SiteConfig.
  """
  site_params = SiteParameters()
  defaults = DefaultSettings(site_params)

  ge_build_config = config_lib.LoadGEBuildConfigFromFile()

  # TODO: Use this to stop generating unnecessary configs for release branches.
  is_release_branch = ge_build_config[config_lib.CONFIG_TEMPLATE_RELEASE_BRANCH]

  hw_test_list = HWTestList(is_release_branch)

  # site_config with no templates or build configurations.
  site_config = config_lib.SiteConfig(defaults=defaults,
                                      site_params=site_params)

  CreateBuilderTemplates(site_config, hw_test_list, is_release_branch)

  board_configs = CreateBoardConfigs(site_config, ge_build_config)

  ToolchainBuilders(site_config, board_configs, hw_test_list)

  ReleaseBuilders(site_config, board_configs, ge_build_config,
                  is_release_branch)

  AddPayloadTryjobs(site_config)

  SpecialtyBuilders(site_config)

  # Fill in templates and build configurations.
  _GetConfig(site_config, board_configs, hw_test_list)

  AndroidPfqBuilders(site_config, board_configs, hw_test_list)

  PreCqBuilders(site_config, board_configs, hw_test_list)

  # Insert default HwTests for tryjobs.
  for build in site_config.itervalues():
    InsertHwTestsOverrideDefaults(build, hw_test_list)

  # Assign waterfalls to builders that don't have them yet.
  InsertWaterfallDefaults(site_config, is_release_branch)

  ApplyCustomOverrides(site_config, hw_test_list)

  return site_config
