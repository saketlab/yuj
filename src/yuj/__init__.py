"""yuj: scatter a batch across opportunistic SSH targets and gather it back."""

from __future__ import annotations

from yuj.authorize import AuthorizeResult, authorize_fleet, authorize_key
from yuj.bootstrap import BootstrapConfig, BootstrapResult, bootstrap, bootstrap_fleet
from yuj.config import ProjectConfig
from yuj.decommission import (
    DecommissionResult,
    decommission,
    schedule_decommission,
)
from yuj.deploy import DeployPlan, DeployResult, deploy, deploy_fleet
from yuj.exceptions import (
    AuthError,
    CommandTimeout,
    FleetError,
    SplitError,
    TransportError,
    YujError,
)
from yuj.fleet import Fleet, Host, load_from_csv, load_from_yaml
from yuj.keys import read_public_key
from yuj.local import LocalTransport
from yuj.probe import (
    classify_host,
    diagnose_fleet,
    probe_fleet,
    probe_host,
)
from yuj.provision import (
    ProvisionConfig,
    ProvisionResult,
    generate_keypair,
    provision,
    provision_fleet,
)
from yuj.pull import PullResult, pull_once
from yuj.scaffolds import scaffold_files
from yuj.scatter import (
    ScatterResult,
    plan_scatter,
    read_items,
    scatter_fleet,
    scatter_host,
)
from yuj.split import Assignment, chunked, pending_items, redistribute, weighted_split
from yuj.status import Diagnosis, HostStatus
from yuj.supervise import (
    SubmitResult,
    SuperviseConfig,
    stop,
    submit,
    submit_fleet,
)
from yuj.transport import SSHTransport, Transport, make_transport
from yuj.window import Window

__all__ = [
    "Assignment",
    "AuthError",
    "AuthorizeResult",
    "BootstrapConfig",
    "BootstrapResult",
    "CommandTimeout",
    "DecommissionResult",
    "DeployPlan",
    "DeployResult",
    "Diagnosis",
    "Fleet",
    "FleetError",
    "Host",
    "HostStatus",
    "LocalTransport",
    "ProjectConfig",
    "ProvisionConfig",
    "ProvisionResult",
    "PullResult",
    "SSHTransport",
    "ScatterResult",
    "SplitError",
    "SubmitResult",
    "SuperviseConfig",
    "Transport",
    "TransportError",
    "Window",
    "YujError",
    "__version__",
    "authorize_fleet",
    "authorize_key",
    "bootstrap",
    "bootstrap_fleet",
    "chunked",
    "classify_host",
    "decommission",
    "deploy",
    "deploy_fleet",
    "diagnose_fleet",
    "generate_keypair",
    "load_from_csv",
    "load_from_yaml",
    "make_transport",
    "pending_items",
    "plan_scatter",
    "probe_fleet",
    "probe_host",
    "provision",
    "provision_fleet",
    "pull_once",
    "read_items",
    "read_public_key",
    "redistribute",
    "scaffold_files",
    "scatter_fleet",
    "scatter_host",
    "schedule_decommission",
    "stop",
    "submit",
    "submit_fleet",
    "weighted_split",
]

__version__ = "0.1.0.dev0"
