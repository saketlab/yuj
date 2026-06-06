"""yuj: scatter a batch across opportunistic SSH targets and gather it back."""

from __future__ import annotations

from yuj.bootstrap import BootstrapConfig, BootstrapResult, bootstrap, bootstrap_fleet
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
from yuj.probe import (
    Diagnosis,
    HostStatus,
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
from yuj.split import Assignment, chunked, pending_items, redistribute, weighted_split
from yuj.supervise import (
    SubmitResult,
    SuperviseConfig,
    stop,
    submit,
    submit_fleet,
)
from yuj.transport import SSHTransport

__all__ = [
    "Assignment",
    "AuthError",
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
    "ProvisionConfig",
    "ProvisionResult",
    "PullResult",
    "SSHTransport",
    "SplitError",
    "SubmitResult",
    "SuperviseConfig",
    "TransportError",
    "YujError",
    "__version__",
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
    "pending_items",
    "probe_fleet",
    "probe_host",
    "provision",
    "provision_fleet",
    "pull_once",
    "redistribute",
    "scaffold_files",
    "schedule_decommission",
    "stop",
    "submit",
    "submit_fleet",
    "weighted_split",
]

__version__ = "0.1.0.dev0"
