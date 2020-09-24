from scrape_series import run_series
import time, glob
import shutil

outfilename = "qb_study_guide.txt"

seriesUrls = [
    "https://university.quickbase.com/series/best-practice",
    "https://university.quickbase.com/series/quickbase-fundamentals",
    "https://university.quickbase.com/series/intermediate-training",
    "https://university.quickbase.com/series/advanced-training",
    "https://university.quickbase.com/series/account-administration"
]

for url in seriesUrls:
    run_series(url, False)

with open(outfilename, 'wb') as outfile:
    for filename in glob.glob('scrape_results/*'):
        if filename == outfilename:
            # don't want to copy the output into the output
            continue
        with open(filename, 'rb') as readfile:
            shutil.copyfileobj(readfile, outfile)
