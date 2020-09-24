import json
import requests
# import demjson
from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from webdriver_manager.chrome import ChromeDriverManager

browser = webdriver.Chrome(ChromeDriverManager().install())

# browser = webdriver.Chrome()  # replace with .Firefox(), or with the browser of your choice
base_city = 'denver'
# regions = ["abilene","akroncanton","albanyga","albany","albuquerque","altoona","amarillo","ames","anchorage","annapolis","annarbor","appleton","asheville","ashtabula","athensga","athensohio","atlanta","auburn","augusta","austin","bakersfield","baltimore","batonrouge","battlecreek","beaumont","bellingham","bemidji","bend","billings","binghamton","bham","bismarck",
regions = ["bloomington","bn","boise","boone","boston","boulder","bgky","bozeman","brainerd","brownsville","brunswick","buffalo","butte","capecod","catskills","cedarrapids","cenla","centralmich","cnj","chambana","charleston","charlestonwv","charlotte","charlottesville","chattanooga","chautauqua","chicago","chico","chillicothe","cincinnati","clarksville","cleveland","clovis","collegestation","cosprings","columbiamo","columbia","columbusga","columbus","cookeville","corpuschristi","corvallis","chambersburg","dallas","danville","daytona","dayton","decatur","nacogdoches","delaware","delrio","denver","desmoines","detroit","dothan","dubuque","duluth","eastco","newlondon","eastky","montana","eastnc","martinsburg","easternshore","eastidaho","eastoregon","eauclaire","elko","elmira","elpaso","erie","eugene","evansville","fairbanks","fargo","farmington","fayar","fayetteville","fingerlakes","flagstaff","flint","shoals","florencesc","keys","fortcollins","fortdodge","fortsmith","fortwayne","frederick","fredericksburg","fresno","fortmyers","gadsden","gainesville","galveston","glensfalls","goldcountry","grandforks","grandisland","grandrapids","greatfalls","greenbay","greensboro","greenville","gulfport","hanford","harrisburg","harrisonburg","hartford","hattiesburg","honolulu","cfl","helena","hickory","rockies","hiltonhead","holland","houma","houston","hudsonvalley","humboldt","huntington","huntsville","imperial","indianapolis","inlandempire","iowacity","ithaca","jxn","jackson","jacksontn","jacksonville","onslow","janesville","jerseyshore","jonesboro","joplin","kalamazoo","kalispell","kansascity","kenai","kpr","racine","killeen","kirksville","klamath","knoxville","kokomo","lacrosse","lafayette","tippecanoe","lakecharles","lakeland","loz","lancaster","lansing","laredo","lasalle","lascruces","lasvegas","lawrence","lawton","allentown","lewiston","lexington","limaohio","lincoln","littlerock","logan","longisland","losangeles","louisville","lubbock","lynchburg","macon","madison","maine","ksu","mankato","mansfield","masoncity","mattoon","mcallen","meadville","medford","memphis","mendocino","merced","meridian","milwaukee","minneapolis","missoula","mobile","modesto","mohave","monroe","monroemi","monterey","montgomery","morgantown","moseslake","muncie","muskegon","myrtlebeach","nashville","nh","newhaven","neworleans","blacksburg","newyork","norfolk","lakecity","nd","nesd","nmi","wheeling","northernwi","newjersey","northmiss","northplatte","nwct","nwga","nwks","enid","ocala","odessa","ogden","okaloosa","oklahomacity","olympic","omaha","oneonta","orangecounty","oregoncoast","orlando","outerbanks","owensboro","palmsprings","panamacity","parkersburg","pensacola","peoria","philadelphia","phoenix","csd","pittsburgh","plattsburgh","poconos","porthuron","portland","potsdam","prescott","provo","pueblo","pullman","quadcities","raleigh","rapidcity","reading","redding","reno","providence","richmondin","richmond","roanoke","rmn","rochester","rockford","roseburg","roswell","sacramento","saginaw","salem","salina","saltlakecity","sanangelo","sanantonio","sandiego","sandusky","slo","sanmarcos","santabarbara","santafe","santamaria","sarasota","savannah","scottsbluff","scranton","seattle","sfbay","sheboygan","showlow","shreveport","sierravista","siouxcity","siouxfalls","siskiyou","skagit","southbend","southcoast","sd","juneau","ottumwa","seks","semo","carbondale","smd","swv","miami","southjersey","swks","swmi","marshall","natchez","bigbend","swva","spacecoast","spokane","springfieldil","springfield","pennstate","statesboro","staugustine","stcloud","stgeorge","stillwater","stjoseph","stlouis","stockton","susanville","syracuse","tallahassee","tampa","terrehaute","texarkana","texoma","thumb","toledo","topeka","treasure","tricities","tucson","tulsa","tuscaloosa","tuscarawas","twinfalls","twintiers","easttexas","up","utica","valdosta","ventura","vermont","victoriatx","visalia","waco","washingtondc","waterloo","watertown","wausau","wenatchee","quincy","westky","westmd","westernmass","westslope","wv","wichitafalls","wichita","williamsport","wilmington","winchester","winstonsalem","worcester","wyoming","yakima","york","youngstown","yubasutter","yuma","zanesville"]

main_link = ".craigslist.org/search/cta?"
param_string = "query=sprinter&hasPic=1&srchType=T&bundleDuplicates=1&min_price=3000&max_price=30000"
rows_xpath = "//div/ul[contains(@class, 'rows')]"
img_xpath = '//*[@id="sortable-results"]/ul/li/a/div[1]/div/div/img'
nearby_0_xpath = '//*[@id="nearbyArea_0"]/parent::label'
area_options_xpath = '//ul/li/select[@id="areaAbb"]/option'


distance_api_url = "https://www.distance24.org/route.json?stops="

def get_data(region):
    url = "https://" + region  + main_link + param_string
    browser.get(url)
    region_main_city = get_main_city(region, browser)
    distance = get_distance(base_city, region_main_city)
    
    if distance < 1200:
        append_distance(distance, region_main_city)
        region_listings = get_listings(region, browser)
        write_file(region_listings)
    else:
        print(region_main_city + ' too far at ' + str(distance) + 'mi')
    
    browser.find_element_by_tag_name('body').send_keys(Keys.COMMAND + 'w')
    
def append_distance(distance, city):
    browser.execute_script("var list = document.querySelectorAll('.result-row > a > .result-price');for (var i = 0; i < list.length; i++) {list[i].textContent = '"+city+"' + ', ' + '"+str(distance)+"' + 'mi, ' + list[i].textContent}")

def get_listings(region, browser):
    try:
        WebDriverWait(browser, 8).until(EC.presence_of_element_located((By.XPATH, img_xpath)))
    except: 
        print('no listings')
        return ""
    rows = browser.find_element_by_xpath(rows_xpath)
    items = rows.get_attribute("innerHTML")
    return items
    
def get_main_city(region, browser):
    main_city = region
    distance = get_distance(base_city, region)
    print("trying region for dist: " + main_city + ' / ' + str(distance) + "mi")
    area_options_els = browser.find_elements_by_xpath(area_options_xpath)
    area_options = list(map(lambda x: x.get_attribute("textContent"), area_options_els))
    if base_city in area_options:
        print("base_city in area_options", region)
        if not distance:
            distance = 0
    else:
        i = 0
        while (not distance) and i < len(area_options):
            main_city = area_options[i]
            distance = get_distance(base_city, main_city)
            print(main_city + ' / ' + str(distance) + "mi")
            i = i + 1
        if distance is 0:
            print("could not find dist, ", region)
            import pdb; pdb.set_trace()
            
    return main_city
    
def get_distance(c1, c2):
    url = distance_api_url + c1 + "|" + c2
    res = requests.get(url)
    data = json.loads(res.text.encode('utf-8'))
    dist = data.get('distance')
    print('dist', dist)
    return dist

def write_file(region_listings):
    resFile = open("res.html", "a")
    resFile.write(region_listings.encode('utf-8'))
    resFile.close()

for region in regions:
    print('region: ' + region)
    get_data(region)

browser.quit()

# get_data('amarillo')

