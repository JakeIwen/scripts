from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

from util import xpath_exists
from util import kill_alert
from util import open_with_cookies
from util import click_except
from util import click_wait
from util import array_to_file
from scrape_lesson import run_scrape
# import scrape_lesson

seriesUrl = "https://university.quickbase.com/series/quickbase-fundamentals"
browser = webdriver.Chrome()  # replace with .Firefox(), or with the browser of your choice
cookies_path = 'cookies.json'


def get_assessment_link(lessonUrl):
    mainUrl = browser.current_url
    browser.get(lessonUrl)

    assessEl = WebDriverWait(browser, 8).until(EC.presence_of_element_located((By.XPATH, '//a[div/div/text()[contains(.,"Assessment")]]')))
    # import pdb; pdb.set_trace()  # Python 2
    print "el href:"
    assessLink = assessEl.get_attribute("href")
    print assessLink
    browser.get(mainUrl)
    print("got link, navigating back to series\n", assessLink)
    return assessLink


def get_series_assessment_links(lessonLinks):
    assessmentLinks = []
    missingLinks = []
    for lessonLink in lessonLinks:
        try:
            newLink = get_assessment_link(lessonLink)
            print("got assessment link: ", newLink)
            assessmentLinks.append(newLink)
        except:
            print("couldnt get assessment link")
            browser.get(browser.current_url.replace("/series/intermediate-training", ""))
            missingLinks.append(lessonLink)
    array_to_file(missingLinks, "no_assessment_link_found.txt")
    array_to_file(assessmentLinks, "assessment_links.txt")
    return assessmentLinks

def run_series(seriesUrl, useFile):

    def is_link_to_lesson(linkEl):
        return (seriesUrl + '/') in linkEl.get_attribute("href")
    open_with_cookies(browser, seriesUrl, cookies_path)

    if useFile:
        assessmentLinks = open('assessment_links.txt', 'r').read().split("\n")
    else:
        elems = browser.find_elements_by_xpath("//a[@href]")
        elems = filter(is_link_to_lesson, elems)
        lessonLinks = []
        for elem in elems:
            lessonLinks.append(elem.get_attribute("href"))
            print("lessonlink: ", elem.get_attribute("href"))
        assessmentLinks = get_series_assessment_links(lessonLinks)
    # seriesTitle = seriesUrl.split("/")[-1].replace("-", " ").upper()
    for link in assessmentLinks:
        fileName = "./scrape_results/" + link.split("/")[-2]
        print("\n\nnew filename:", fileName)
        seriesAnswersFile = open(fileName, "w+")
        run_scrape(browser, link, seriesAnswersFile)
        seriesAnswersFile.close()
