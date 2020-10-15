
function filterSortRows(params){
  var {
    rowSelector, textElementSelector, bannedText, reqText, sort, isAscending
  } = params;
  
  $(rowSelector).sort((row1, row2) => {
    var result = 0;
    var t1 = $(row1).find(textElementSelector).text();
    
    var hasBannedWords = bannedText.some(txt => t1.toLowerCase().includes(txt.toLowerCase()))
    var hasRequiredWords = reqText.every(txt => t1.toLowerCase().includes(txt.toLowerCase()))
    
    if (hasBannedWords || !hasRequiredWords) {
      console.log(`Hiding row with text: ${t1}`)
      $(row1).hide();
    }
    
    if (sort) {
      var t2 = $(row2).find(textElementSelector).text();
      var result = (t1 > t2) ? 1 : -1;
      if (!isAscending) result *= -1;
    }
    
    return result

  }).appendTo($(rowSelector).parent());
  console.log('done.')
}

function run(params) {
  console.log(params)
  
  if ((typeof jQuery) !== 'undefined') {
    return filterSortRows(params);
  }
  // load jQuery if site does not have it. 
  var jq = document.createElement('script');
  jq.src = "https://ajax.googleapis.com/ajax/libs/jquery/2.1.4/jquery.min.js";
  document.getElementsByTagName('head')[0].appendChild(jq);
  jq.onload = () => filterSortRows(params)
}

run({
  rowSelector: '#torrents tr',
  textElementSelector: '.t_ctime', // child of row
  bannedText: ['crime'], //hides rows with any these words
  reqText: [], //hides rows without all of these words
  sort: true, // sort rows by the text within
  isAscending: false, //sort diretion
}); 
