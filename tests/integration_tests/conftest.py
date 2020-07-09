import os
import pytest
import logging
import wagon

import integration_tests_plugins

from collections import namedtuple
from integration_tests.tests import utils as test_utils
from integration_tests.framework import docker
from integration_tests.framework.utils import zip_files
from integration_tests.framework.amqp_events_printer import EventsPrinter
from integration_tests.framework.flask_utils import \
    prepare_reset_storage_script, reset_storage


logger = logging.getLogger('TESTENV')
Env = namedtuple('Env', ['container_id', 'container_ip'])


def pytest_addoption(parser):
    parser.addoption(
        '--image-name',
        help='Name of the Cloudify Manager AIO docker image',
        default='cloudify-manager-aio:latest'
    )
    parser.addoption(
        '--keep-container',
        help='Do not delete the container after tests finish',
        default=False,
        action='store_true'
    )
    parser.addoption(
        '--tests-source-root',
        help='Directory containing cloudify sources to mount',
    )
    parser.addoption(
        '--container-id',
        help='Run integration tests on this container',
    )


# items from tests-source-root to be mounted into the specified
# on-manager virtualenvs
# pairs of (source path, [list of target virtualenvs])
# TODO fill this in as needed, when needed
sources = [
    ('cloudify-common/cloudify', ['/opt/manager/env', '/opt/mgmtworker/env']),
    ('cloudify-common/dsl_parser', ['/opt/manager/env']),
    ('cloudify-common/script_runner', ['/opt/mgmtworker/env']),
    ('cloudify-agent/cloudify_agent', ['/opt/mgmtworker/env']),
    ('cloudify-manager/mgmtworker/mgmtworker', ['/opt/mgmtworker/env']),
    ('cloudify-manager/rest-service/manager_rest', ['/opt/manager/env']),
    ('cloudify-manager/rest-service/manager_rest', ['/opt/manager/env']),
    ('cloudify-manager/workflows/cloudify_system_workflows', ['/opt/mgmtworker/env']),  # NOQA
    ('cloudify-manager-install/cfy_manager', ['/opt/cloudify/cfy_manager'])
]


def _sources_mounts(request):
    """Mounts for the provided sources.

    The caller can pass --tests-source-root and some directories from
    there will be mounted into the appropriate on-manager venvs.
    """
    sources_root = request.config.getoption("--tests-source-root")
    if not sources_root:
        return
    for src, target_venvs in sources:
        src = os.path.abspath(os.path.join(sources_root, src))
        if os.path.exists(src):
            yield (src, target_venvs)


@pytest.fixture(scope='session')
def resource_mapping(request):
    resources = []
    for src, target_venvs in _sources_mounts(request):
        for venv in target_venvs:
            dst = os.path.join(
                venv, 'lib', 'python3.6', 'site-packages',
                os.path.basename(src))
            resources += [(src, dst)]
    yield resources


@pytest.fixture(scope='session')
def manager_container(request, resource_mapping):
    image_name = request.config.getoption("--image-name")
    keep_container = request.config.getoption("--keep-container")
    container_id = request.config.getoption("--container-id")
    if container_id:
        _clean_manager(container_id)
    else:
        container_id = docker.run_manager(
            image_name, resource_mapping=resource_mapping)
        docker.upload_mock_license(container_id)
    container_ip = docker.get_manager_ip(container_id)
    container = Env(container_id, container_ip)
    prepare_reset_storage_script(container_id)
    amqp_events_printer_thread = EventsPrinter(
        docker.get_manager_ip(container_id))
    amqp_events_printer_thread.start()
    try:
        yield container
    finally:
        if not keep_container:
            docker.clean(container_id)


@pytest.fixture(scope='session')
def rest_client(manager_container):
    client = test_utils.create_rest_client(host=manager_container.container_ip)
    yield client


@pytest.fixture(scope='class')
def manager_class_fixtures(request, manager_container, rest_client):
    """Just a hack to put some fixtures on the test class.

    This is for compatibility with class-based tests, who don't have
    a better way of using fixtures. Eventually, those old tests will
    transition to be function-based, and they won't need to use this.
    """
    request.cls.env = manager_container
    request.cls.client = rest_client


def _clean_manager(container_id):
    dirs_to_clean = [
        '/opt/mgmtworker/work/deployments',
        '/opt/manager/resources/blueprints',
        '/opt/manager/resources/uploaded-blueprints'
    ]
    reset_storage(container_id)
    for directory in dirs_to_clean:
        docker.execute(
            container_id,
            ['sh', '-c', 'rm -rf {0}/*'.format(directory)])


@pytest.fixture(autouse=True)
def prepare_manager_storage(request, manager_container):
    """Make sure that for each test, the manager storage is the same.

    This involves uploading the license before the tests, and
    cleaning the db & storage directories between tests.
    """
    container_id = manager_container.container_id
    try:
        yield
    finally:
        request.session.testsfinished = \
            getattr(request.session, 'testsfinished', 0) + 1
        if request.session.testsfinished != request.session.testscollected:
            _clean_manager(container_id)


@pytest.fixture(scope='session')
def allow_agent(manager_container, package_agent):
    """Allow installing an agent on the manager container.

    Agent installation scripts have all kinds of assumptions about
    sudo and su, so those need to be available.
    """
    docker.execute(manager_container.container_id, [
        'bash', '-c',
        "echo 'cfyuser ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/cfyuser"
    ])
    docker.execute(manager_container.container_id, [
        'sed', '-i',
        "1iauth sufficient pam_succeed_if.so user = cfyuser",
        '/etc/pam.d/su'
    ])


@pytest.fixture(scope='session')
def package_agent(manager_container, request):
    """Repackage the on-manager agent with the provided sources.

    If the user provides sources (--tests-source-root), then
    not only are those mounted into the mgmtworker and manager,
    but they will also be put into the agent package that is used in
    the tests.

    All the sources that are mounted to the mgmtworker env, will
    also be used for the agent.
    """
    # unpack the agent archive, overwrite files, repack it, copy back
    # to the package location
    mgmtworker_env = '/opt/mgmtworker/env/lib/python*/site-packages/'
    agent_package = \
        '/opt/manager/resources/packages/agents/centos-core-agent.tar.gz'
    agent_source_path = 'cloudify/env/lib/python*/site-packages/'
    sources = []
    for src, target_venvs in _sources_mounts(request):
        if '/opt/mgmtworker/env' in target_venvs:
            sources.append(os.path.basename(src))
    if not sources:
        return
    docker.execute(manager_container.container_id, [
        'bash', '-c', 'cd /tmp && tar xvf {0}'.format(agent_package)
    ])
    for package in sources:
        source = os.path.join(mgmtworker_env, package)
        target = os.path.join('/tmp', agent_source_path)
        docker.execute(manager_container.container_id, [
            'bash', '-c',
            'cp -fr {0} {1}'.format(source, target)
        ])
    docker.execute(manager_container.container_id, [
        'bash', '-c',
        'cd /tmp && tar czf centos-core-agent.tar.gz cloudify'
    ])
    docker.execute(manager_container.container_id, [
        'mv', '-f',
        '/tmp/centos-core-agent.tar.gz',
        agent_package
    ])


@pytest.fixture(scope='function')
def workdir(request, tmpdir):
    request.cls.workdir = tmpdir


def _make_wagon_fixture(plugin_name):
    """Prepare a session-scoped fixture that creates a plugin wagon."""
    @pytest.fixture(scope='session')
    def _fixture(rest_client, tmp_path_factory):
        plugins_dir = os.path.dirname(integration_tests_plugins.__file__)
        wagon_path = wagon.create(
            os.path.join(plugins_dir, plugin_name),
            archive_destination_dir=str(tmp_path_factory.mktemp(plugin_name)),
            force=True
        )
        yaml_path = os.path.join(plugins_dir, plugin_name, 'plugin.yaml')
        with zip_files([wagon_path, yaml_path]) as zip_path:
            yield zip_path
    _fixture.__name__ = '{0}_wagon'.format(plugin_name)
    return _fixture


def _make_upload_plugin_fixture(plugin_name):
    """Prepare a function-scoped fixture that uploads the plugin.

    That fixture will use the scoped-session wagon fixture and upload it.
    """
    # use exec to be able to dynamically name the parameter. So that
    # this fixture uses the right wagon fixture.
    d = {}
    exec("""
def {0}_plugin(rest_client, {0}_wagon):
    rest_client.plugins.upload({0}_wagon)
""".format(plugin_name), d)
    func = d['{0}_plugin'.format(plugin_name)]
    return pytest.fixture()(func)


cloudmock_wagon = _make_wagon_fixture('cloudmock')
cloudmock_plugin = _make_upload_plugin_fixture('cloudmock')
testmockoperations_wagon = _make_wagon_fixture('testmockoperations')
testmockoperations_plugin = _make_upload_plugin_fixture('testmockoperations')
get_attribute_wagon = _make_wagon_fixture('get_attribute')
get_attribute_plugin = _make_upload_plugin_fixture('get_attribute')
dockercompute_wagon = _make_wagon_fixture('dockercompute')
dockercompute_plugin = _make_upload_plugin_fixture('dockercompute')
target_aware_mock_wagon = _make_wagon_fixture('target_aware_mock')
target_aware_mock_plugin = _make_upload_plugin_fixture('target_aware_mock')
