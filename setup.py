from setuptools import find_packages, setup


setup(
    name="crab-archi-design",
    version="0.1.0",
    description="Original-SVG-first architectural community design copilot framework.",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    extras_require={"test": ["pytest>=8"]},
    entry_points={
        "console_scripts": [
            "crab-archi-design=crab_archi_design.cli:main",
            "crab-archi-design-mcp=crab_archi_design.mcp_server:main",
        ]
    },
)
