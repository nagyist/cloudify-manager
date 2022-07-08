import os

import pytest

from cloudify.constants import NODE_INSTANCE, RELATIONSHIP_INSTANCE
from cloudify.exceptions import NonRecoverableError
from cloudify.workflows import local


def dsl_path_base():
    return os.path.join(os.path.dirname(__file__), 'blueprints')


def deploy(blueprint_filename, resource_id='d1', storage=None, inputs=None):
    if storage is None:
        storage = local.InMemoryStorage()
    blueprint_path = os.path.join(dsl_path_base(), blueprint_filename)
    storage.create_blueprint(resource_id, blueprint_path)
    storage.create_deployment(resource_id, resource_id, inputs=inputs)
    return storage


def test_empty_update():
    storage = deploy('empty.yaml', resource_id='d1')
    dep_env = local.load_env('d1', storage)
    storage.create_deployment_update('d1', 'update1', {})
    dep_env.execute('update', parameters={'update_id': 'update1'})
    executions = dep_env.storage.get_executions()
    assert len(executions) == 1
    assert executions[0]['workflow_id'] == 'update'
    assert executions[0]['status'] == 'terminated'


def test_run_update_workflow():
    storage = deploy('empty.yaml', resource_id='d1')
    storage.create_blueprint(
        'bp2',
        os.path.join(dsl_path_base(), 'description.yaml'),
    )
    original_description = storage.get_deployment('d1').description
    dep_env = local.load_env('d1', storage)
    storage.create_deployment_update('d1', 'update1', {
        'new_blueprint_id': 'bp2',
    })
    dep_env.execute('update', parameters={'update_id': 'update1'})
    changed_description = storage.get_deployment('d1').description
    assert changed_description != original_description


def test_change_property():
    storage = deploy('property_input.yaml', resource_id='d1', inputs={
        'inp1': 'value1',
    })
    dep_env = local.load_env('d1', storage)
    storage.create_deployment_update('d1', 'update1', {
        'new_inputs': {'inp1': 'value2'}
    })
    dep_env.execute('update', parameters={'update_id': 'update1'})
    # reload the storage to get the updated deployment + node
    dep_env = local.load_env('d1', storage)
    node = dep_env.storage.get_node('n1', evaluate_functions=True)
    assert node.properties['prop1'] == 'value2'


@pytest.mark.parametrize('blueprint_filename,parameters', [
    ('update_operation.yaml', {}),
    ('skip_drift_check.yaml', {
        'skip_drift_check': True,
    }),
])
def test_update_operation(subtests, blueprint_filename, parameters):
    """Test the update instances flow.

    This function is essentially just the driver code, and the actual cases
    and expectations are encoded in the blueprint itself.
    """
    storage = deploy(blueprint_filename, resource_id='d1', inputs={
        'inp1': 'value1',
    })
    dep_env = local.load_env('d1', storage)
    storage.create_deployment_update('d1', 'update1', {
        'new_inputs': {'inp1': 'value2'}
    })
    parameters['update_id'] = 'update1'
    dep_env.execute('update', parameters=parameters)

    for ni in dep_env.storage.get_node_instances():
        # if ni.node_id != 'n2':
        #     continue
        with subtests.test(ni.node_id):
            node = dep_env.storage.get_node(ni.node_id)
            actual_calls = ni.runtime_properties.get('invocations', [])
            expected_calls = node.properties.get('expected_calls') or []
            assert actual_calls == expected_calls


def op(ctx, return_value=None, fail=False):
    """Operation used in the update-operation test.

    Store the call in runtime-properties, and return the given value, or fail.
    """
    if ctx.type == RELATIONSHIP_INSTANCE:
        if ctx._context['related']['is_target']:
            prefix = 'target_'
            instance = ctx.target.instance
        else:
            prefix = 'source_'
            instance = ctx.source.instance
    elif ctx.type == NODE_INSTANCE:
        instance = ctx.instance
        prefix = ''
    else:
        raise NonRecoverableError(f'unknown ctx.type: {ctx.type}')

    if 'invocations' not in instance.runtime_properties:
        instance.runtime_properties['invocations'] = []
    name = prefix + ctx.operation.name.split('.')[-1]
    invocations = instance.runtime_properties['invocations']
    invocations.append(name)
    instance.runtime_properties['invocations'] = invocations
    if fail:
        raise NonRecoverableError()
    return return_value
