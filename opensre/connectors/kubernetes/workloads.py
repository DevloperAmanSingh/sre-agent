from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list, conditions, container_details
from opensre.connectors.kubernetes.models import DeploymentDetail, DeploymentSummary, Revision
from opensre.connectors.kubernetes.reader import KubeReader, Page, bounded_page, bounded_response


def label_selector(selector: Any) -> str:
    labels: dict[str, str] = selector.match_labels or {}
    parts = [f"{key}={value}" for key, value in sorted(labels.items())]
    for expression in as_list(selector.match_expressions):
        if expression.operator in {"In", "NotIn"}:
            operator = "in" if expression.operator == "In" else "notin"
            parts.append(f"{expression.key} {operator} ({','.join(expression.values or [])})")
        elif expression.operator == "Exists":
            parts.append(expression.key)
        elif expression.operator == "DoesNotExist":
            parts.append(f"!{expression.key}")
    return ",".join(parts)


def workload_tools(reader: KubeReader, apps_factory: Callable[[Any], Any]) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def k8s_list_deployments(
        namespace: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> tuple[str, Page[DeploymentSummary]]:
        """Read desired, ready, available and updated deployment replica counts."""

        def read(api: Any) -> Page[DeploymentSummary]:
            page = apps_factory(api).list_namespaced_deployment(
                namespace=namespace or reader.settings.namespace,
                limit=limit,
                _request_timeout=reader.settings.request_timeout_s,
            )
            return bounded_page(
                page,
                limit,
                lambda deployment: DeploymentSummary(
                    name=deployment.metadata.name,
                    desired=deployment.spec.replicas or 0,
                    ready=deployment.status.ready_replicas or 0,
                    available=deployment.status.available_replicas or 0,
                    updated=deployment.status.updated_replicas or 0,
                ),
            )

        result = reader.read(read)
        return bounded_response(result)

    @tool(response_format="content_and_artifact")
    def k8s_describe_deployment(
        name: str, namespace: str | None = None
    ) -> tuple[str, DeploymentDetail]:
        """Read deployment images, env names, resources, probes, strategy and conditions."""

        def read(api: Any) -> DeploymentDetail:
            deployment = apps_factory(api).read_namespaced_deployment(
                name=name,
                namespace=namespace or reader.settings.namespace,
                _request_timeout=reader.settings.request_timeout_s,
            )
            specs = as_list(deployment.spec.template.spec.init_containers) + as_list(
                deployment.spec.template.spec.containers
            )
            return DeploymentDetail(
                name=deployment.metadata.name,
                namespace=deployment.metadata.namespace,
                strategy=deployment.spec.strategy.type if deployment.spec.strategy else None,
                containers=[container_details(spec) for spec in specs[:50]],
                conditions=conditions(deployment.status.conditions),
                cut={
                    "containers": max(0, len(specs) - 50),
                    "conditions": max(0, len(deployment.status.conditions or []) - 50),
                },
            )

        result = reader.read(read)
        return bounded_response(result)

    @tool(response_format="content_and_artifact")
    def k8s_rollout_history(
        name: str, namespace: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 50
    ) -> tuple[str, Page[Revision]]:
        """Read owned ReplicaSet revisions, change causes and images from a bounded page."""

        def read(api: Any) -> Page[Revision]:
            apps = apps_factory(api)
            ns = namespace or reader.settings.namespace
            timeout = reader.settings.request_timeout_s
            deployment = apps.read_namespaced_deployment(
                name=name, namespace=ns, _request_timeout=timeout
            )
            page = apps.list_namespaced_replica_set(
                namespace=ns,
                limit=limit,
                label_selector=label_selector(deployment.spec.selector),
                _request_timeout=timeout,
            )

            def revision(replica: Any) -> Revision:
                annotations: dict[str, str] = replica.metadata.annotations or {}
                value = annotations.get("deployment.kubernetes.io/revision", "")
                specs = as_list(replica.spec.template.spec.containers)
                return Revision(
                    revision=int(value) if value.isdigit() else None,
                    change_cause=annotations.get("kubernetes.io/change-cause"),
                    images=[spec.image for spec in specs[:50]],
                    images_cut=max(0, len(specs) - 50),
                )

            result = bounded_page(page, limit, revision)
            result.items = [
                row
                for replica, row in zip(page.items[:limit], result.items, strict=True)
                if any(
                    owner.uid == deployment.metadata.uid
                    and owner.kind == "Deployment"
                    and owner.controller
                    for owner in as_list(replica.metadata.owner_references)
                )
            ]
            result.items.sort(key=lambda row: row.revision or 0)
            result.truncation = (
                f"Matched {len(result.items)} revisions; ReplicaSets {result.truncation}"
            )
            return result

        result = reader.read(read)
        return bounded_response(result)

    return [k8s_list_deployments, k8s_describe_deployment, k8s_rollout_history]
