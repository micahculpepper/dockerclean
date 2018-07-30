from setuptools import setup

VERSION = "0.0.4"
README = open("README.rst").read()

setup(
    author="Micah Culpepper",
    author_email="micahculpepper@gmail.com",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Topic :: System :: Systems Administration",
    ],
    entry_points={
        "console_scripts": [
            "dockerclean = dockerclean:main"
        ]
    },
    description="Carefully remove unused docker artifacts",
    extras_require={
        "completions": [
            "argcomplete"
        ]
    },
    install_requires=[
        "python-dateutil"
    ],
    keywords=[
        "docker",
        "clean",
        "container",
    ],
    license="Apache License",
    long_description=README,
    long_description_content_type="text/x-rst; charset=UTF-8",
    name="dockerclean",
    packages=[
        "dockerclean.py"
    ],
    platforms=[
        "Linux",
        "MacOS",
    ],
    python_requires=">=2.7.5,!=3.0.*,!=3.1.*,!=3.2.*,!=3.3.*,!=3.4.*,>=3.5",
    url="https://github.com/micahculpepper/dockerclean",
    version=VERSION,
    zip_safe="False",
)
