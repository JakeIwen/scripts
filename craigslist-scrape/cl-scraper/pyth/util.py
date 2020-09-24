from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

import json
# import pdb; pdb.set_trace()  # pdb.set_trace = lambda: None

def array_to_file(array, filePath):
    file = open(filePath, "w+")
    for stgItem in array:
        file.write(stgItem + "\n")
    file.close()


def append_unique(item, ary):
    if ("     [   ] " + item) not in ary:
        if ("     [ C ] " + item) not in ary:
            if ("     [ S ] " + item) not in ary:
                ary.append(item)
    return ary

def aria_label_exists(browser, label):
    try:
        WebDriverWait(browser, 15).until(
            EC.presence_of_all_elements_located((By.XPATH, "//*[@aria-label='" + label + "']"))
        )
        return True
    except:
        return False

def click_next_btn(browser):
    try:
        click_wait(browser, '//*[text()=">> "]/../..')
    except:
        print "nextBtn timeout"

def click_except(browser, selector):
    attempts = 0
    while attempts < 3:
        try:
            el = WebDriverWait(browser, 6).until(EC.presence_of_element_located((By.XPATH, selector)))
            el.click()
        except:
            attempts += 1
            print("click exception attempt", attempts, "selector:", selector)
            continue
        break

def xpath_exists(browser, xpath, wait=1):
    try:
        WebDriverWait(browser, wait).until(EC.presence_of_all_elements_located((By.XPATH, xpath)))
        return True
    except:
        return False

# def wait_for_xpath_removal(xpath):

def click_wait(browser, selector):
    el = WebDriverWait(browser, 10).until(EC.presence_of_element_located((By.XPATH, selector)))
    el.click()
    # WebDriverWait(browser, 10).until(EC.staleness_of(el))

def kill_alert(browser):
    try:
        WebDriverWait(browser, 30).until(EC.alert_is_present())
        browser.switch_to.alert.dismiss()
    except:
        print "No alert present"

def open_with_cookies(browser, url, cookieJsonPath):
    cookie_data = open(cookieJsonPath).read()
    cookies = json.loads(cookie_data)
    browser.get(url)  # navigate to the page
    for cookie in cookies:
        browser.add_cookie(cookie)
    browser.get(url)  # navigate to the page
