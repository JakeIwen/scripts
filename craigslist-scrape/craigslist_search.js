var regions = ["abilene","akroncanton","albanyga","albany","albuquerque","altoona","amarillo","ames","anchorage","annapolis","annarbor","appleton","asheville","ashtabula","athensga","athensohio","atlanta","auburn","augusta","austin","bakersfield","baltimore","batonrouge","battlecreek","beaumont","bellingham","bemidji","bend","billings","binghamton","bham","bismarck","bloomington","bn","boise","boone","boston","boulder","bgky","bozeman","brainerd","brownsville","brunswick","buffalo","butte","capecod","catskills","cedarrapids","cenla","centralmich","cnj","chambana","charleston","charlestonwv","charlotte","charlottesville","chattanooga","chautauqua","chicago","chico","chillicothe","cincinnati","clarksville","cleveland","clovis","collegestation","cosprings","columbiamo","columbia","columbusga","columbus","cookeville","corpuschristi","corvallis","chambersburg","dallas","danville","daytona","dayton","decatur","nacogdoches","delaware","delrio","denver","desmoines","detroit","dothan","dubuque","duluth","eastco","newlondon","eastky","montana","eastnc","martinsburg","easternshore","eastidaho","eastoregon","eauclaire","elko","elmira","elpaso","erie","eugene","evansville","fairbanks","fargo","farmington","fayar","fayetteville","fingerlakes","flagstaff","flint","shoals","florencesc","keys","fortcollins","fortdodge","fortsmith","fortwayne","frederick","fredericksburg","fresno","fortmyers","gadsden","gainesville","galveston","glensfalls","goldcountry","grandforks","grandisland","grandrapids","greatfalls","greenbay","greensboro","greenville","gulfport","hanford","harrisburg","harrisonburg","hartford","hattiesburg","honolulu","cfl","helena","hickory","rockies","hiltonhead","holland","houma","houston","hudsonvalley","humboldt","huntington","huntsville","imperial","indianapolis","inlandempire","iowacity","ithaca","jxn","jackson","jacksontn","jacksonville","onslow","janesville","jerseyshore","jonesboro","joplin","kalamazoo","kalispell","kansascity","kenai","kpr","racine","killeen","kirksville","klamath","knoxville","kokomo","lacrosse","lafayette","tippecanoe","lakecharles","lakeland","loz","lancaster","lansing","laredo","lasalle","lascruces","lasvegas","lawrence","lawton","allentown","lewiston","lexington","limaohio","lincoln","littlerock","logan","longisland","losangeles","louisville","lubbock","lynchburg","macon","madison","maine","ksu","mankato","mansfield","masoncity","mattoon","mcallen","meadville","medford","memphis","mendocino","merced","meridian","milwaukee","minneapolis","missoula","mobile","modesto","mohave","monroe","monroemi","monterey","montgomery","morgantown","moseslake","muncie","muskegon","myrtlebeach","nashville","nh","newhaven","neworleans","blacksburg","newyork","norfolk","lakecity","nd","nesd","nmi","wheeling","northernwi","newjersey","northmiss","northplatte","nwct","nwga","nwks","enid","ocala","odessa","ogden","okaloosa","oklahomacity","olympic","omaha","oneonta","orangecounty","oregoncoast","orlando","outerbanks","owensboro","palmsprings","panamacity","parkersburg","pensacola","peoria","philadelphia","phoenix","csd","pittsburgh","plattsburgh","poconos","porthuron","portland","potsdam","prescott","provo","pueblo","pullman","quadcities","raleigh","rapidcity","reading","redding","reno","providence","richmondin","richmond","roanoke","rmn","rochester","rockford","roseburg","roswell","sacramento","saginaw","salem","salina","saltlakecity","sanangelo","sanantonio","sandiego","sandusky","slo","sanmarcos","santabarbara","santafe","santamaria","sarasota","savannah","scottsbluff","scranton","seattle","sfbay","sheboygan","showlow","shreveport","sierravista","siouxcity","siouxfalls","siskiyou","skagit","southbend","southcoast","sd","juneau","ottumwa","seks","semo","carbondale","smd","swv","miami","southjersey","swks","swmi","marshall","natchez","bigbend","swva","spacecoast","spokane","springfieldil","springfield","pennstate","statesboro","staugustine","stcloud","stgeorge","stillwater","stjoseph","stlouis","stockton","susanville","syracuse","tallahassee","tampa","terrehaute","texarkana","texoma","thumb","toledo","topeka","treasure","tricities","tucson","tulsa","tuscaloosa","tuscarawas","twinfalls","twintiers","easttexas","up","utica","valdosta","ventura","vermont","victoriatx","visalia","waco","washingtondc","waterloo","watertown","wausau","wenatchee","quincy","westky","westmd","westernmass","westslope","wv","wichitafalls","wichita","williamsport","wilmington","winchester","winstonsalem","worcester","wyoming","yakima","york","youngstown","yubasutter","yuma","zanesville"]

var cors = ["abilene","albanyga","albany","albuquerque","altoona","amarillo","ames","anchorage","annapolis","annarbor","appleton","asheville","ashtabula","athensga","athensohio","atlanta","auburn","augusta","austin","bakersfield","baltimore","batonrouge","battlecreek","beaumont","bellingham","bemidji","bend","billings","binghamton","bham","bismarck","bloomington","bn","boise","boone","boston","boulder","bgky","bozeman","brainerd","brownsville","brunswick","buffalo","butte","capecod","catskills","cedarrapids","cenla","centralmich","cnj","charleston","charlestonwv","charlotte","charlottesville","chattanooga","chautauqua","chicago","chico","chillicothe","cincinnati","clarksville","cleveland","clovis","collegestation","cosprings","columbiamo","columbia","columbusga","columbus","cookeville","corpuschristi","corvallis","chambersburg","dallas","elpaso","erie","eugene","evansville","fairbanks","fargo","farmington","fayar","fayetteville","fingerlakes","flagstaff","flint","shoals","florencesc","keys","fortcollins","fortdodge","fortsmith","fortwayne","frederick","fredericksburg","fresno","fortmyers","gadsden","gainesville","galveston","glensfalls","goldcountry","grandforks","grandisland","grandrapids","greatfalls","greenbay","greensboro","greenville","gulfport","hanford","harrisburg","harrisonburg","hartford","hattiesburg","honolulu","cfl","helena","hickory","rockies","hiltonhead","holland","houma","houston","hudsonvalley","humboldt","huntington","huntsville","imperial","indianapolis","inlandempire","iowacity","ithaca","jxn","jackson","jacksontn","jacksonville","onslow","janesville","jerseyshore","jonesboro","joplin","kalamazoo","kalispell","kansascity","kenai","kpr","racine","killeen","kirksville","klamath","knoxville","kokomo","lacrosse","lafayette","tippecanoe","lakecharles","lakeland","loz","lancaster","lansing","laredo","lasalle","lascruces","lasvegas","lawrence","lawton","allentown","lewiston","lexington","limaohio","lincoln","littlerock","logan","longisland","losangeles","louisville","lubbock","lynchburg","macon","madison","maine","ksu","mankato","mansfield","masoncity","mattoon","mohave","monroe","monroemi","monterey","montgomery","morgantown","moseslake","muncie","muskegon","myrtlebeach","nashville","nh","neworleans","blacksburg","northplatte","nwct","nwga","nwks","enid","ocala","odessa","ogden","okaloosa","oklahomacity","olympic","omaha","oneonta","orangecounty","oregoncoast","orlando","outerbanks","owensboro","palmsprings","panamacity","parkersburg","pensacola","peoria","philadelphia","phoenix","csd","pittsburgh","plattsburgh","poconos","porthuron","portland","potsdam","prescott","provo","pueblo","pullman","quadcities","raleigh","rapidcity","reading","redding","reno","providence","richmondin","richmond","roanoke","rmn","rochester","rockford","roseburg","roswell","sacramento","saginaw","salem","salina","saltlakecity","sanangelo","sanantonio","sandiego","sandusky","slo","sanmarcos","santabarbara","santafe","santamaria","sarasota","savannah","scottsbluff","scranton","seattle","sfbay","sheboygan","showlow","shreveport","sierravista","siouxcity","siouxfalls","siskiyou","skagit","southbend","southcoast","sd","juneau","ottumwa","seks","semo","carbondale","smd","swv","springfield","tuscarawas","twinfalls","twintiers","up","utica","valdosta","ventura","vermont","victoriatx","visalia","waco","washingtondc","waterloo","watertown","wausau","wenatchee","quincy","westky","westmd","westernmass","westslope","wv","wichitafalls","wichita",]

var foundRegions = ["akroncanton", "chambana", "danville", "daytona", "dayton", "decatur", "nacogdoches", "delaware", "delrio", "denver", "desmoines", "detroit", "dothan", "dubuque", "duluth", "eastco", "newlondon", "eastky", "montana", "eastnc", "martinsburg", "easternshore", "eastidaho", "eastoregon", "eauclaire", "elko", "elmira", "mcallen", "meadville", "medford", "memphis", "mendocino", "merced", "meridian", "milwaukee", "minneapolis", "missoula", "mobile", "modesto", "newhaven", "newyork", "norfolk", "lakecity", "nd", "nesd", "nmi", "wheeling", "northernwi", "newjersey", "northmiss", "miami", "southjersey", "swks", "swmi", "marshall", "natchez", "bigbend", "swva", "spacecoast", "spokane", "springfieldil", "pennstate", "statesboro", "staugustine", "stcloud", "stgeorge", "stillwater", "stjoseph", "stlouis", "stockton", "susanville", "syracuse", "tallahassee", "tampa", "terrehaute", "texarkana", "texoma", "thumb", "toledo", "topeka", "treasure", "tricities", "tucson", "tulsa", "tuscaloosa", "easttexas", "williamsport", "wilmington", "winchester", "winstonsalem", "worcester", "wyoming", "yakima", "york", "youngstown", "yubasutter", "yuma", "zanesville"]



var jq = document.createElement('script');
jq.src = "https://ajax.googleapis.com/ajax/libs/jquery/2.1.4/jquery.min.js";
document.getElementsByTagName('head')[0].appendChild(jq);

setTimeout(()=> runCode(), 1500);
var pages = {};

async function runCode(){
  
  var params = $.param({
    query: "sprinter",
    // auto_make_model: 'dodge+sprinter',
    // sort: "rel",
    hasPic: 1,
    srchType: 'T',
    // searchNearby: 1,
    bundleDuplicates: 1,
    min_price: 3000,
    max_price: 30000
  })
  
  var mainLink = ".craigslist.org/search/cta?" + params
  // var regions = ["denver", "grandisland", "bismarck", "roswell", "saltlakecity", "lawton", "butte", "phoenix", "minneapolis"] 
  
  var newPageSl = '.content ul.rows';
  $(newPageSl).empty()
  
  await addPage(regions.shift())
  
  var $mainList = $(newPageSl);

  // while(regions.length){
  //   await Promise.all(regions.splice(0,25).map(l => addPage(l)))
  // }
  
  loadPics();

  function addPage(region){
    var href = "https://" + region + mainLink;
    return new Promise((resolve) => {
      
      
      $.ajax({
        method: 'GET',
        url: 'https://crossorigin.me/' + href,
        success: (res) => {
          console.log({res});
          var parser = new DOMParser();
          var doc = parser.parseFromString(res, "text/html");
          pages[region] = doc
          var items = doc.getElementsByClassName('result-row');
          console.log('items', region, items.length);
          $(items).find('.result-price').append(` <span match-region="${region}"></span>`)
          $(items).find('[data-index]').not('[data-index="0"]').remove()

          if($('body').children().length){
           $mainList.append(items);
          } else {
           var body = $(pages[region].document.getElementsByTagName('body')[0])
           $('body').append(body.children());
          }
          resolve(true);
        }
      })
      // if(!pages[region]) resolve(true)
      // $(pages[region].document).ready( () => {
      //   setTimeout(() => {
      //     try{
      //       var items = $(pages[region].document.getElementsByClassName('result-row'));
      //       $(items).find('.result-price').append(` <span match-region="${region}"></span>`)
      //       $(items).find('[data-index]').not('[data-index="0"]').remove()
      //       console.log('items', region, items.length);
      //       if($('body').children().length){
      //         $mainList.append(items);
      //       } else {
      //         var body = $(pages[region].document.getElementsByTagName('body')[0])
      //         $('body').append(body.children());
      //       }
      //     }catch(err){
      // 
      //       pages[region].close();
      //     }
      // 
      //     pages[region].close();
      //     resolve(true);
      //   }, 3000)
      // 
      // })
    })

  }
  

  var list = document.querySelectorAll('.result-row > a > .result-price');for (var i = 0; i < list.length; i++) {list[i].textContent = city + ', ' + distance + 'mi, ' + list[i].textContent}
  function jsonCallback(json, el){
    var city = json.stops[0].city.toLowerCase();
    if(city == 'denver') city = json.stops[q].city.toLowerCase();
    distances[city]= json.distance;
    
    var matches = $(`span[match-region='${city}']`);  
    $(matches[0]).text(json.distance+'mi');
  }
  function dist(c1,c2, el){
    console.log('getting distance', {c1,c2,el});
    var val = $.ajax({
      url: encodeURI(`https://www.distance24.org/route.json?stops=${c1}|${c2}&callback=jsonCallback&jsonp=jsonCallback`),
      dataType: "jsonp",
	     jsonp: "callback"
    }).then( res => {
      jsonCallback(res, el)
    } );
  }

  function getDistances(){
    $('span[match-region]').empty()
    foundRegions.forEach(c1 => dist(c1, 'denver'))
  }
  var distances = {};
  getDistances();

}






(function removeDuplicates(){
  var pids = []
  $(`.result-row[data-pid]`).each(function(){
    var pid = $(this).attr('data-pid');
    if(pids.includes(pid)) $(this).remove();
    else pids.push(pid)
  })
})()

(function loadPics(){
  $('.swipe-wrap').attr('style', 'width: 7000px;')
  $('.swipe-wrap div:first-child').attr('style', 'width: 300px; left: 0px; transition-duration: 0ms; transform: translate(0px, 0px);')
  $('.result-row > a:visible').each(function(){
    if($(this).find('.swipe-wrap').length) return
    var src = 'https://images.craigslist.org/' + $(this).attr('data-ids').split(':')[1].split(',')[0] + '_300x300.jpg'
    $(this).append(`<img src=${src} />`);
  })
})()


function getVal(el, idx){
  return parseInt($(el).find('a > .result-price').text().split(',')[idx])
}

function sortBy(attr){
  var idx = 0;
  if(attr === 'distance') idx = 1;
  else if(attr === 'price') idx = 2;
  
  var sortResult = $('.result-row').sort((a,b) => {
    return getVal(a, idx) > getVal(b, idx) ? 1 : -1
  })
  
  $('.content').empty().append(sortResult)
}


sortBy('distance')


regions.forEach(r => !cors.includes(r) && foundRegions.push(r));