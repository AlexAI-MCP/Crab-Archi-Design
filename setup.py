from setuptools import find_packages, setup
from setuptools.command.build_py import build_py
from pathlib import Path


class BuildWithAssets(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "crab_archi_design" / "assets"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("canvas.html", "canvas_3d.js", "studio.html", "doodle_editor.html"):
            self.copy_file(str(Path(__file__).parent / "tools" / name), str(target / name))


setup(
    name="crab-archi-design",
    version="0.2.0",
    description="Original-SVG-first architectural community design copilot framework.",
    package_dir={"": "src"},
    packages=find_packages("src"),
    cmdclass={"build_py": BuildWithAssets},
    python_requires=">=3.9",
    install_requires=["defusedxml>=0.7.1", "ezdxf>=1.3"],
    extras_require={"test": ["pytest>=8"]},
    entry_points={
        "console_scripts": [
            "crab-archi-design=crab_archi_design.cli:main",
            "crab-archi-design-mcp=crab_archi_design.mcp_server:main",
            "crabcadparser=crab_archi_design.cad_cli:main",
            "crabcadparser-mcp=crab_archi_design.cad_mcp_server:main",
        ]
    },
)
