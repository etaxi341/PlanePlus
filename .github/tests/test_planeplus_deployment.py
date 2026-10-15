"""Guard fork-specific publishing settings against upstream merge regressions.

Run with: python -m unittest discover -s .github/tests -v (requires PyYAML).
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
IMAGES = {
    "web": "planeplus-frontend",
    "space": "planeplus-space",
    "admin": "planeplus-admin",
    "live": "planeplus-live",
    "api": "planeplus-backend",
    "proxy": "planeplus-proxy",
    "aio": "planeplus-aio-community",
}
BUILD_ACTION = "makeplane/actions/build-push@398c0632195865906c6839b0292c67b9fd2a301e"


def read_yaml(path):
    # BaseLoader preserves GitHub's `on` key instead of treating it as a YAML 1.1 boolean.
    return yaml.load((ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class TestPlanePlusDeployment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = read_yaml(".github/workflows/build-branch.yml")

    def test_publish_jobs_are_guarded_and_use_fork_images(self):
        jobs = self.workflow["jobs"]
        setup = jobs["branch_build_setup"]
        self.assertEqual(
            setup["if"],
            "${{ github.repository == 'etaxi341/PlanePlus' && github.ref == 'refs/heads/master' }}",
        )
        setup_script = setup["steps"][0]["run"]
        self.assertIn("BUILDX_ENDPOINT=johannmnn/planeplus-dev", setup_script)
        for component, image in IMAGES.items():
            with self.subTest(component=component):
                job = jobs[f"branch_build_push_{component}"]
                self.assertIn("branch_build_setup", job["needs"])
                steps = [step for step in job["steps"] if step.get("uses") == BUILD_ACTION]
                self.assertEqual(len(steps), 1, "Publishing action must stay pinned")
                inputs = steps[0]["with"]
                self.assertEqual(inputs["docker-image-owner"], "johannmnn")
                for flag in ("build-release", "build-prerelease", "release-version"):
                    output_key = flag.replace("-", "_")
                    self.assertEqual(inputs[flag], "${{ needs.branch_build_setup.outputs." + output_key + " }}")
                output = "backend" if component == "api" else component
                self.assertEqual(
                    inputs["docker-image-name"],
                    "${{ needs.branch_build_setup.outputs.dh_img_" + output + " }}",
                )
                self.assertIn(f"DH_IMG_{output.upper()}={image}", setup_script)

    def test_automatic_workflows_target_master_only(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = read_yaml(path)
            for event in ("push", "pull_request"):
                config = workflow.get("on", {}).get(event) or {}
                if "branches" in config:
                    with self.subTest(workflow=path.name, event=event):
                        self.assertEqual(config["branches"], ["master"])
        self.assertEqual(self.workflow["on"]["push"]["branches"], ["master"])

    def test_upstream_preview_cannot_publish_from_fork(self):
        workflow = read_yaml(".github/workflows/feature-deployment.yml")
        self.assertEqual(
            workflow["jobs"]["branch_build_setup"]["if"],
            "${{ github.repository == 'makeplane/plane' }}",
        )
        self.assertIn("branch_build_setup", workflow["jobs"]["full_build_push"]["needs"])

    def test_deployed_services_use_fork_images(self):
        for path in ("deployments/cli/community/docker-compose.yml", "deployments/dockge/compose.yaml"):
            services = read_yaml(path)["services"]
            for component, image in IMAGES.items():
                if component == "aio":
                    continue
                with self.subTest(path=path, component=component):
                    self.assertIn(f"/{image}:", services[component]["image"])
                    self.assertIn("johannmnn", services[component]["image"])
            for component in ("worker", "beat-worker", "migrator"):
                self.assertEqual(services[component]["image"], services["api"]["image"])

    def test_aio_only_embeds_planeplus_images(self):
        dockerfile = (ROOT / "deployments/aio/community/Dockerfile").read_text(encoding="utf-8")
        sources = re.findall(r"^FROM (\S+) AS (?:web|backend|space|admin|live|proxy)-img$", dockerfile, re.M)
        self.assertEqual(
            set(sources),
            {f"johannmnn/{image}:${{PLANE_VERSION}}" for key, image in IMAGES.items() if key != "aio"},
        )
        self.assertIn("ARG PLANE_VERSION=latest", dockerfile)
        script = (ROOT / "deployments/aio/community/build.sh").read_text(encoding="utf-8")
        self.assertIn("IMAGE_NAME=${IMAGE_NAME:-johannmnn/planeplus-aio-community}", script)

    def test_installers_download_from_fork(self):
        for path in ("deployments/cli/community/install.sh", "deployments/swarm/community/swarm.sh"):
            with self.subTest(path=path):
                script = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("export GH_REPO=etaxi341/PlanePlus", script)
                self.assertIn("export DOCKERHUB_USER=johannmnn", script)
                self.assertNotIn("makeplane/plane", script)
                self.assertNotIn("DOCKERHUB_USER=makeplane", script)

    def test_aio_and_asset_tags_match_published_component_tag(self):
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(git_bash) if os.name == "nt" and git_bash.exists() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required to exercise the workflow's tag selection")
        steps = self.workflow["jobs"]["branch_build_push_aio"]["steps"]
        script = next(step["run"] for step in steps if step.get("id") == "prepare_aio_assets")
        aio_script = script[script.index('if [ "'):script.index("bash ./build.sh")]
        steps = self.workflow["jobs"]["upload_build_assets"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == "Update Assets")
        asset_script = script[:script.index("cp ./deployments")]
        cases = (
            ("", "master", "latest", "latest"),
            ("Build", "master", "latest", "latest"),
            ("Release", "master", "v1.4.3", "v1.4.3"),
            ("Release", "master", "v1.4.3-rc-1", "v1.4.3-rc-1"),
        )
        for variable, script in (("aio_version", aio_script), ("REL_VERSION", asset_script)):
            for build_type, branch, release, expected in cases:
                with self.subTest(variable=variable, build_type=build_type, branch=branch, release=release):
                    rendered = script
                    outputs = {"build_type": build_type, "gh_branch_name": branch, "release_version": release}
                    for key, value in outputs.items():
                        rendered = rendered.replace("${{ needs.branch_build_setup.outputs." + key + " }}", value)
                    # Feed Bash via stdin to avoid platform-specific command-line quoting.
                    result = subprocess.run(
                        [bash, "-s"], input=rendered + f'\nprintf "%s" "${variable}"',
                        text=True, capture_output=True, check=True,
                    )
                    self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
