#!/usr/bin/env python3
"""Synthetic regression tests for match_images.py."""

import unittest

from match_images import CatalogRepo, analyze_rows, match_one


CATALOG = [
    CatalogRepo("nginx", "BASE", ("base",), ("docker.io/library/nginx:latest",), frozenset({"1.25", "latest"}), "1"),
    CatalogRepo("openssl-fips", "FIPS", ("fips",), ("docker.io/library/openssl:latest",), frozenset({"3.0", "latest"}), "2"),
    CatalogRepo("redis", "APPLICATION", ("application",), ("docker.io/library/redis:latest",), frozenset({"7.2", "latest"}), "3"),
    CatalogRepo("team/widget", "UNKNOWN", (), (), frozenset({"1.0"}), "4"),
    CatalogRepo("other/widget", "APPLICATION", (), (), frozenset({"1.0"}), "5"),
    CatalogRepo("postgresql", "BASE", (), (), frozenset({"16"}), "6"),
    CatalogRepo(
        "cert-manager-acmesolver",
        "APPLICATION",
        ("application",),
        ("quay.io/jetstack/cert-manager-controller:latest",),
        frozenset({"v1.19.3", "latest"}),
        "7",
    ),
    CatalogRepo(
        "cert-manager-controller",
        "APPLICATION",
        ("application",),
        ("quay.io/jetstack/cert-manager-controller:latest",),
        frozenset({"v1.19.3", "latest"}),
        "8",
    ),
    CatalogRepo(
        "cert-manager-controller-iamguarded",
        "APPLICATION",
        ("application",),
        ("quay.io/jetstack/cert-manager-controller:latest",),
        frozenset({"v1.19.3", "latest"}),
        "9",
    ),
    CatalogRepo(
        "cert-manager-webhook",
        "APPLICATION",
        ("application",),
        ("quay.io/jetstack/cert-manager-controller:latest",),
        frozenset({"v1.19.3", "latest"}),
        "10",
    ),
    CatalogRepo("keda", "APPLICATION", ("application",), ("kedacore/keda:latest",), frozenset({"2.19.0", "latest"}), "11"),
    CatalogRepo("keda", "UNKNOWN", (), (), frozenset({"2.19.0"}), "12"),
    CatalogRepo(
        "metrics-server",
        "APPLICATION",
        ("application",),
        ("registry.k8s.io/metrics-server/metrics-server:latest",),
        frozenset({"v0.8.0", "latest"}),
        "13",
    ),
    CatalogRepo("metrics-server", "UNKNOWN", (), (), frozenset(), "14"),
    CatalogRepo("metrics-server-iamguarded", "APPLICATION", ("application",), (), frozenset({"v0.8.0", "latest"}), "15"),
    CatalogRepo("dask-kubernetes-operator", "APPLICATION", ("application",), (), frozenset({"1.0"}), "16"),
    CatalogRepo("spark-kubernetes-operator", "APPLICATION", ("application",), (), frozenset({"1.0"}), "17"),
    CatalogRepo("redis-iamguarded", "APPLICATION", ("application",), (), frozenset({"7", "latest"}), "18"),
    CatalogRepo("nginx-iamguarded", "APPLICATION", ("application",), (), frozenset({"latest"}), "19"),
    CatalogRepo(
        "datadog-agent",
        "APPLICATION",
        ("application",),
        ("docker.io/datadog/agent",),
        frozenset({"7.73.0", "latest"}),
        "20",
    ),
    CatalogRepo(
        "datadog-cluster-agent",
        "APPLICATION",
        ("application",),
        ("docker.io/datadog/agent",),
        frozenset({"7.73.0", "latest"}),
        "21",
    ),
    CatalogRepo(
        "datadog-agent-fips",
        "FIPS",
        ("fips",),
        ("docker.io/datadog/agent",),
        frozenset({"7.73.0", "latest"}),
        "22",
    ),
    CatalogRepo("github-runner-agent", "APPLICATION", ("application",), (), frozenset({"latest"}), "23"),
    CatalogRepo("newrelic-infra-agent", "APPLICATION", ("application",), (), frozenset({"latest"}), "24"),
]


class MatcherTests(unittest.TestCase):
    def test_reordered_multilevel_headers_and_all_outcomes(self):
        rows = [
            ["Synthetic intake only"],
            ["Required", "Customer shares", "Customer shares", "Output"],
            ["FIPS Reqd.", "Version(s)", "Container / Image", "Chaingaurd Equivalent", "Progress Status"],
            ["No", "1.25", "nginx", "", ""],
            ["Yes", "3.0", "openssl", "", ""],
            ["No", "7.2", "registry-1.docker.io/library/redis:7.2", "", ""],
            ["No", "6.0", "redis", "", ""],
            ["No", "1.0", "registry.example.com/widget", "", ""],
            ["No", "latest", "does-not-exist", "", ""],
            ["", "", "Total", "", ""],
        ]
        result = analyze_rows(rows, CATALOG, None)
        self.assertEqual(result["header_row"], 2)
        self.assertEqual(result["input_columns"]["image"], 2)
        statuses = [row["match"]["status"] for row in result["row_results"]]
        self.assertEqual(statuses, [
            "exact_image_exact_tag",
            "exact_image_exact_tag",
            "exact_image_exact_tag",
            "image_available_tag_unavailable",
            "multiple_possible_matches",
            "no_match",
        ])
        self.assertEqual(result["row_results"][1]["match"]["candidates"], ["openssl-fips"])
        self.assertTrue(result["row_results"][0]["match"]["equivalent"].startswith("cgr.dev/chainguard-private/"))

    def test_destination_org_overrides_chainguard_private_default(self):
        default_match = match_one("nginx:1.25", "", "no", CATALOG, None)
        override_match = match_one("nginx:1.25", "", "no", CATALOG, "acme")
        self.assertEqual(default_match["equivalent"], "cgr.dev/chainguard-private/nginx:1.25")
        self.assertEqual(override_match["equivalent"], "cgr.dev/acme/nginx:1.25")

    def test_explicit_non_fips_excludes_fips_alias(self):
        rows = [["Image", "FIPS", "Version"], ["openssl", "No", "3.0"]]
        result = analyze_rows(rows, CATALOG, None)
        self.assertEqual(result["row_results"][0]["match"]["status"], "no_match")

    def test_duplicate_rows_are_preserved_and_cached(self):
        rows = [["Image Name", "Tag"], ["nginx", "1.25"], ["nginx", "1.25"]]
        result = analyze_rows(rows, CATALOG, None)
        self.assertEqual(result["summary"]["processed_rows"], 2)
        self.assertEqual(result["summary"]["unique_requests"], 1)

    def test_alternate_reordered_headers(self):
        rows = [
            ["Synthetic intake"],
            ["Tracking status", "Source container", "FIPS compliance", "Release", "CG Match"],
            ["", "nginx", "No", "1.25", ""],
        ]
        result = analyze_rows(rows, CATALOG, None)
        self.assertEqual(result["input_columns"], {
            "progress": 0,
            "image": 1,
            "fips": 2,
            "version": 3,
            "equivalent": 4,
        })
        self.assertEqual(result["row_results"][0]["match"]["status"], "exact_image_exact_tag")

    def test_unclassified_repository_is_not_called_customer_ready(self):
        rows = [["Image Name", "Tag"], ["team/widget", "1.0"]]
        match = analyze_rows(rows, CATALOG, None)["row_results"][0]["match"]
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertEqual(match["verdict"], "Exists; readiness unclassified")
        self.assertIn("not evidence of customer readiness", match["notes"])

    def test_unique_fuzzy_candidate_requires_review(self):
        rows = [["Image Name", "Tag"], ["postgressql", "16"]]
        match = analyze_rows(rows, CATALOG, None)["row_results"][0]["match"]
        self.assertEqual(match["status"], "possible_match_review")
        self.assertEqual(match["equivalent"], "")

    def test_alias_family_autofills_matching_final_component(self):
        match = match_one("quay.io/jetstack/cert-manager-controller:v1.19.3", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertEqual(match["candidates"], ["cert-manager-controller"])
        self.assertIn("cert-manager-controller", match["equivalent"])
        self.assertNotIn("iamguarded", match["equivalent"])

    def test_duplicate_catalog_names_collapse_and_autofill(self):
        match = match_one("ghcr.io/kedacore/keda:2.19.0", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertEqual(match["candidates"], ["keda"])
        self.assertIn("/keda:2.19.0", match["equivalent"])

    def test_metrics_server_prefers_non_iamguarded(self):
        match = match_one("registry.k8s.io/metrics-server/metrics-server:v0.8.0", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertEqual(match["candidates"], ["metrics-server"])
        self.assertNotIn("iamguarded", match["equivalent"])

    def test_unrelated_operator_fuzzy_left_blank(self):
        match = match_one("twingate/kubernetes-operator:1.1.2", "", "no", CATALOG, None)
        self.assertIn(match["status"], {"multiple_possible_matches", "no_match", "possible_match_review"})
        self.assertEqual(match["equivalent"], "")

    def test_bitnami_prefers_iamguarded(self):
        match = match_one("docker.io/bitnami/redis:7", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertIn("redis-iamguarded", match["equivalent"])

    def test_non_bitnami_does_not_prefer_iamguarded(self):
        match = match_one("nginx:1.25", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertIn("/nginx:1.25", match["equivalent"])
        self.assertNotIn("iamguarded", match["equivalent"])

    def test_generic_final_with_path_token_autofills_across_registries(self):
        for source in (
            "public.ecr.aws/datadog/agent:latest",
            "gcr.io/datadoghq/agent:7.73.0",
            "datadog/agent:latest",
        ):
            with self.subTest(source=source):
                match = match_one(source, "", "no", CATALOG, None)
                self.assertEqual(match["status"], "exact_image_exact_tag")
                self.assertEqual(match["candidates"], ["datadog-agent"])
                self.assertIn("/datadog-agent", match["equivalent"])
                self.assertNotIn("cluster-agent", match["equivalent"])
                self.assertNotIn("fips", match["equivalent"])

    def test_generic_final_without_supporting_path_token_stays_blank(self):
        match = match_one("acme/agent:latest", "", "no", CATALOG, None)
        self.assertIn(match["status"], {"multiple_possible_matches", "no_match", "possible_match_review"})
        self.assertEqual(match["equivalent"], "")

    def test_namespaced_exact_final_uses_path_token(self):
        match = match_one("registry.example.com/team/widget:1.0", "", "no", CATALOG, None)
        self.assertEqual(match["status"], "exact_image_exact_tag")
        self.assertEqual(match["candidates"], ["team/widget"])


if __name__ == "__main__":
    unittest.main()
