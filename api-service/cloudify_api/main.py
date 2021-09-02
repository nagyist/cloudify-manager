import pkg_resources

import cloudify_api
from cloudify_api.routers import audit as audit_router


DEBUG = False


def create_application() -> cloudify_api.CloudifyAPI:
    pkg = pkg_resources.require(cloudify_api.__package__)[0]
    application = cloudify_api.CloudifyAPI(
        title=pkg._parsed_pkg_info.get('Summary'),
        version=pkg.version,
        debug=DEBUG
    )
    application.configure()
    application.include_router(audit_router, prefix="/api/v3.1")
    return application


app = create_application()