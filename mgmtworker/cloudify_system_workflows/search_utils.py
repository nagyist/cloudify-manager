from copy import copy

from cloudify.exceptions import NonRecoverableError
from dsl_parser.constants import BLUEPRINT_OR_DEPLOYMENT_ID_CONSTRAINT_TYPES


class GetValuesWithRest:
    def __init__(self, client, blueprint_id=None):
        self.client = client
        self.blueprint_id = blueprint_id

    def has_blueprint_id(self):
        return bool(self.blueprint_id)

    @staticmethod
    def has_deployment_id():
        return False

    def get(self, data_type, value, **kwargs):
        kwargs = self.update_blueprint_id_constraint(data_type, **kwargs)
        if data_type == 'blueprint_id':
            return {b.id for b in self.get_blueprints(value, **kwargs)}
        elif data_type == 'deployment_id':
            return {d.id for d in self.get_deployments(value, **kwargs)}
        elif data_type == 'secret_key':
            return {s.key for s in self.get_secrets(value, **kwargs)}
        elif data_type == 'capability_value':
            return {cap_details['value']
                    for dep_cap in self.get_capability_values(value, **kwargs)
                    for cap in dep_cap['capabilities']
                    for cap_details in cap.values()}
        elif data_type == 'scaling_group':
            return {g.name for g in self.get_scaling_groups(value, **kwargs)}
        elif data_type == 'node_id':
            return {n.id for n in self.get_nodes(value, **kwargs)}
        elif data_type == 'node_type':
            return {n.type for n in self.get_node_types(value, **kwargs)}
        elif data_type == 'node_instance':
            return {n.id for n in self.get_node_instances(value, **kwargs)}
        elif data_type == 'operation_name':
            return set(self.get_operation_names(value, **kwargs))

        raise NotImplementedError("Getter function not defined for "
                                  f"data type '{data_type}'")

    def get_blueprints(self, blueprint_id, **kwargs):
        return self.client.blueprints.list(_search=blueprint_id,
                                           _include=['id'],
                                           _get_all_results=True,
                                           constraints=kwargs)

    def get_deployments(self, deployment_id, **kwargs):
        return self.client.deployments.list(_search=deployment_id,
                                            _include=['id'],
                                            _get_all_results=True,
                                            constraints=kwargs)

    def get_secrets(self, secret_key, **kwargs):
        return self.client.secrets.list(_search=secret_key,
                                        _include=['key'],
                                        _get_all_results=True,
                                        constraints=kwargs)

    def get_capability_values(self, capability_value, **kwargs):
        try:
            deployment_id = kwargs.pop('deployment_id')
        except KeyError:
            raise NonRecoverableError(
                "Parameters of type 'capability_value' require the "
                f"'deployment_id' constraint ({capability_value}).")
        return self.client.deployments.capabilities.list(
            deployment_id,
            _search=capability_value,
            _get_all_results=True,
            constraints=kwargs)

    def get_scaling_groups(self, scaling_group, **kwargs):
        blueprint_id = kwargs.pop('blueprint_id', None)
        deployment_id = kwargs.pop('deployment_id', None)
        if blueprint_id is None and deployment_id is None:
            raise NonRecoverableError(
                "Parameters of type 'scaling_group' require the "
                "'deployment_id' or 'blueprint_id' constraint "
                f"({scaling_group}).")
        return self.client.deployments.scaling_groups.list(
            blueprint_id=blueprint_id,
            deployment_id=deployment_id,
            _search=scaling_group,
            _include=['name'],
            _get_all_results=True,
            constraints=kwargs)

    def get_nodes(self, node_id, **kwargs):
        blueprint_id = kwargs.pop('blueprint_id', None)
        deployment_id = kwargs.pop('deployment_id', None)
        if blueprint_id is None and deployment_id is None:
            raise NonRecoverableError(
                f"Parameters of type 'node_id' require the 'deployment_id' "
                f"or 'blueprint_id' constraint ({node_id}).")
        return self.client.nodes.list(node_id=node_id,
                                      blueprint_id=blueprint_id,
                                      deployment_id=deployment_id,
                                      _include=['id'],
                                      _get_all_results=True,
                                      constraints=kwargs)

    def get_node_types(self, node_type, **kwargs):
        blueprint_id = kwargs.pop('blueprint_id', None)
        deployment_id = kwargs.pop('deployment_id', None)
        if blueprint_id is None and deployment_id is None:
            raise NonRecoverableError(
                f"Parameters of type 'node_type' require the 'deployment_id' "
                f"or 'blueprint_id' constraint ({node_type}).")
        return self.client.nodes.types.list(blueprint_id=blueprint_id,
                                            deployment_id=deployment_id,
                                            node_type=node_type,
                                            _include=['type'],
                                            _get_all_results=True,
                                            constraints=kwargs)

    def get_node_instances(self, node_instance, **kwargs):
        try:
            deployment_id = kwargs.pop('deployment_id')
        except KeyError:
            raise NonRecoverableError(
                "Parameters of type 'node_instance' require the "
                f"'deployment_id' constraint ({node_instance}).")
        return self.client.node_instances.list(deployment_id=deployment_id,
                                               id=node_instance,
                                               _include=['id'],
                                               _get_all_results=True,
                                               constraints=kwargs)

    def get_operation_names(self, operation_name, **kwargs):
        blueprint_id = kwargs.pop('blueprint_id', None)
        deployment_id = kwargs.pop('deployment_id', None)
        if blueprint_id is None and deployment_id is None:
            raise NonRecoverableError(
                "Parameters of type 'operation_name' require the "
                "'deployment_id' or 'blueprint_id' constraint "
                f"({operation_name}).")
        nodes = self.client.nodes.list(
            blueprint_id=blueprint_id,
            deployment_id=deployment_id,
            _include=['operations'],
            _get_all_results=True,
            constraints=kwargs
        )
        results = []
        for node in nodes:
            for name, operation_specs in node['operations'].items():
                if operation_name_matches(
                    name,
                    operation_name,
                    valid_values=kwargs.get('valid_values'),
                    operation_name_specs=kwargs.get('operation_name_specs'),
                ):
                    results.append(name)

        return results

    def update_blueprint_id_constraint(self, data_type, **kwargs):
        if data_type not in BLUEPRINT_OR_DEPLOYMENT_ID_CONSTRAINT_TYPES:
            return kwargs
        params = copy(kwargs)
        if 'blueprint_id' not in kwargs and 'deployment_id' not in kwargs:
            params['blueprint_id'] = self.blueprint_id
        return params


def operation_name_matches(operation_name, search_value,
                           valid_values=None,
                           operation_name_specs=None):
    """Verify if operation_name matches the constraints.

    :param operation_name: name of an operation to test.
    :param search_value: value of an input/parameter of type operation_name,
                         if provided, must exactly match `operation_name`.
    :param valid_values: a list of allowed values for the `operation_name`.
    :param operation_name_specs: a dictionary describing a name_pattern
                                 constraint for `operation_name`.
    :return: `True` if `operation_name` matches the constraints provided with
             the other three parameters.
    """
    if operation_name_specs:
        for operator, value in operation_name_specs.items():
            match operator:
                case 'contains':
                    if value not in operation_name:
                        return False
                case 'starts_with':
                    if not operation_name.startswith(str(value)):
                        return False
                case 'ends_with':
                    if not operation_name.endswith(str(value)):
                        return False
                case 'equals_to':
                    if operation_name != str(value):
                        return False
                case _:
                    raise NotImplementedError('Unknown operation name '
                                              f'pattern operator: {operator}')
    if valid_values:
        if operation_name not in valid_values:
            return False
    if search_value:
        return operation_name == search_value

    return True
