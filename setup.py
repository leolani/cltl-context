from setuptools import setup, find_namespace_packages


with open("README.md", "r") as fh:
    long_description = fh.read()

with open("VERSION", "r") as fh:
    version = fh.read().strip()


setup(
    name='cltl.template',
    version=version,
    package_dir={'': 'src'},
    packages=find_namespace_packages(include=['cltl.*', 'cltl_service.*'], where='src'),
    data_files=[('VERSION', ['VERSION'])],
    url="https://github.com/leolani/cltl-template",
    license='MIT License',
    author='',
    author_email='',
    description='Template component for Leolani',
    long_description=long_description,
    long_description_content_type="text/markdown",
    python_requires='>=3.9',
    install_requires=['cltl.combot'],
    extras_require={
        "impl": [
            "numpy"
        ],
        "service": [
            "emissor",
            "flask",
            "kombu"
        ]}
)
