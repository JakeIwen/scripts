from selenium import webdriver
import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

from util import xpath_exists
from util import click_except
from util import click_wait
from util import click_next_btn

imgSrcCorrect = "correct_answer_normal.png"
imgSrcSkip = "skip_answer_normal.png"
imgSrcIncorrect = "incorrect_answer_normal.png"
cookies_path = 'cookies.json'
startButtonXpath = '//p[text()[contains(.,"Trigger this button to")]]'
radioButtonXpath = "//div[contains(@id,'_highlight')]"
canvasButtonXpath = "//*[contains(@id,'Image_')]"
matchingItemXpath = "//div[@class='cp-matchingItem']"
questionXpaths = [
    '//*[@id="div_Slide"]/div/div/p[text()[contains(.,"?")]]',
    '//*[@id="div_Slide"]/div/div/p[text()[contains(.,"...")]]',
    '//*[@id="div_Slide"]/div/div/p[text()[contains(.,":")]]'
]

def switch_to_iframe(browser):
    element = WebDriverWait(browser, 10).until(EC.presence_of_element_located((By.ID, "scorm_content_frame")))
    browser.switch_to_frame(element)
    print("switched to ", "scorm_content_frame")

def click_all_children(browser, el):
    try:
        children = el.find_elements_by_xpath(".//*")
    except StaleElementReferenceException:
        print "stale return"
        return
    for childEl in children:
        if xpath_exists(browser, startButtonXpath, .1):
            try:
                childEl.click()
                click_all_children(browser, childEl)
            except StaleElementReferenceException:
                print "stale ref except"
                break
            except:
                click_all_children(browser, childEl)
        else:
            print "breaking child loop"
            return


def get_question_text(browser):
    questionText = False
    index = 0
    for xpath in questionXpaths:
        try:
            questionEl = WebDriverWait(browser, 8).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            print("got question, attempt num ", index + 1)
            questionText = questionEl.get_attribute("outerText")
            return questionText
        except:
            print "trying next questionTextEl"
            index += 1
    return questionText


def get_answer_set(browser):
    optionsTextList = False
    while(not optionsTextList):
        inputEls = []
        print "getting inputEls"
        while (not len(inputEls)):
            try:
                inputEls = WebDriverWait(browser, 10).until(EC.presence_of_all_elements_located((By.TAG_NAME, 'input')))
                print("num input elements:", len(inputEls))
            except:
                print "couldnt find inputs"
                return False
        optionsTextList = get_options_text(inputEls) + [""]
    return optionsTextList

def get_options_text(inputElements):
    inputTextList = []
    for input in inputElements:
        imgParent = []
        try:
            optionLabel = input.get_attribute("aria-label")
            imgParent = input.find_element_by_xpath("../../../..")
        except StaleElementReferenceException:
            print "stale label, "
            return False
        try:
            img = imgParent.find_element_by_tag_name("img")
            if imgSrcIncorrect in img.get_attribute("src"):
                text = "     [   ] " + optionLabel  # green checkbox
                print "marking incorrect"
            elif imgSrcCorrect in img.get_attribute("src"):
                text = "     [ C ] " + optionLabel  # green checkbox
            elif imgSrcSkip in img.get_attribute("src"):
                text = "     [ C ] " + optionLabel  # gray checkbox
            else:
                text = "     [   ] " + optionLabel
        except:
            text = "     [   ] " + optionLabel
        print text
        inputTextList.append(text)
    return inputTextList

def next_slide(browser):
    time.sleep(.8)
    click_wait(browser, '//p[text()="Submit "]/../..')
    clickDiv = WebDriverWait(browser, 15).until(
        EC.presence_of_element_located((By.XPATH, "//*[@id='feedbackClickDiv']"))
    )
    clickDiv.click()
    WebDriverWait(browser, 10).until(EC.staleness_of(clickDiv))

def review_is_complete(browser):
    complete = xpath_exists(browser, '//p[text()="Review Assessment "]', 1)
    if complete:
        print "review complete"
        # import pdb; pdb.set_trace()
    return complete

def click_inputs(browser):
    def has_radios():
        return xpath_exists(browser, radioButtonXpath, 5)

    def has_canvas():
        return xpath_exists(browser, canvasButtonXpath, 5)

    def has_matching():
        return xpath_exists(browser, matchingItemXpath, 5)

    missed_clicks = 0
    while (has_radios() or has_canvas() or has_matching()):
        try:
            if has_radios():
                print "click radios"
                click_wait(browser, radioButtonXpath)
            elif has_matching():
                click_wait(browser, '//p[text()="Skip "]/../..')
                print "skipped matching"
            elif has_canvas():
                print "click canvas"
                clickOptions = browser.find_elements_by_xpath(canvasButtonXpath)
                print len(clickOptions)
                for el in clickOptions:
                    try:
                        el.click()
                    except:
                        print "canvas missed click"
                    time.sleep(.2)
            next_slide(browser)
            missed_clicks = 0
        except:
            missed_clicks += 1
            print("missed click num: ", missed_clicks)
            if missed_clicks > 10:
                print "trying next >> btn"
                try:
                    click_next_btn(browser)
                    missed_clicks = 0
                except:
                    print "next btn fail"

def iterate_review(browser, output):
    ctSets = 0
    qCount = 0

    def next_results_page():
        loopCt = 0
        lastQuestion = get_question_text(browser)
        try:
            while((lastQuestion in browser.page_source) and not review_is_complete(browser)):
                print("waiting for next assessment page. this must be removed from DOM", lastQuestion)
                click_next_btn(browser)
                loopCt += 1
                time.sleep(.8)
                if loopCt > 15:
                    print "unknown format. skipping"
                    import pdb; pdb.set_trace()
        except:
            print "error while((lastQuestion in browser.page_source) and not review_is_complete(browser))"
            click_next_btn(browser)
    results = []
    while not review_is_complete(browser):
        if xpath_exists(browser, "//input"):
            print "question iteration"
            try:
                newQuestion = get_question_text(browser)
                answerSet = get_answer_set(browser)
                if newQuestion and answerSet and (newQuestion not in results):
                    print("newQuestion", newQuestion)
                    results.append(newQuestion)
                    results += answerSet
                    ctSets += 1
                else:
                    print("skipping dupe question", newQuestion)
                    click_next_btn(browser)

            except:
                print "FAILED IN TRY ln180"
                import pdb; pdb.set_trace()
        else:
            print "\nno inputs!!\n"
            # import pdb; pdb.set_trace()
        qCount += 1
        next_results_page()

    print 'utf8'
    results = [text.encode("utf8") for text in results]

    print "writing results lines"
    for line in results:
        output.write(line + "\n")

def run_scrape(browser, url, answersFile):
    browser.get(url)
    # kill_alert(browser)
    switch_to_iframe(browser)
    while (not xpath_exists(browser, startButtonXpath, 1)):
        time.sleep(.1)
        print "waiting on start button to show"

    while xpath_exists(browser, startButtonXpath, 1):
        parentEl = WebDriverWait(browser, 10).until(EC.presence_of_element_located((By.ID, "project")))
        print "clicking children"
        click_all_children(browser, parentEl)

    print "clicking inputs"
    click_inputs(browser)
    print "clicking Review assessment "
    click_except(browser, '//p[text()="Review Assessment "]/../..')

    while (xpath_exists(browser, '//p[text()="Review Assessment "]', 1)):
        print "waiting for review btn to disappear"
        time.sleep(.1)
    title = browser.current_url.split(".quickbase.com/")[1].split("/")[0].replace("-", " ").upper()
    print("lesson title: ", title)
    answersFile.write(title + "\n")
    iterate_review(browser, answersFile)
    answersFile.write("\n\n")

    print 'done'

# run_scrape()
