# setup.py
from setuptools import setup, find_packages

setup(
    name="WebNovelScraper",
    version="1.0.0",
    packages=find_packages(),
    package_data={'webnovel_assets': ['*.svg']},
    py_modules=['WebNovelScraper', 'WebNovelScraper_Simple', 'WebNovelDesktop', 'desktop_service'],
    install_requires=[
        'requests>=2.31.0',
        'beautifulsoup4>=4.12.0',
        'EbookLib>=0.18.0',
        'Pillow>=10.0.0',
        'PySide6>=6.8,<7',
    ],
    entry_points={
        'console_scripts': [
            'webnovelscraper=WebNovelScraper:main',
        ],
        'gui_scripts': [
            'webnovelscraper-desktop=WebNovelDesktop:main',
        ],
    },
    author="Your Name",
    author_email="your.email@example.com",
    description="A tool for scraping web novels and converting them to EPUB format",
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type="text/markdown",
    keywords="web novel, scraper, epub, ebook",
    url="https://github.com/yourusername/WebNovelScraper",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',
)
