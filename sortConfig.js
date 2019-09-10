function srt(config){
  return config.split('\n').sort().join('\n');
}

function diff(config1, config2){
  var c1 = config1.split('\n').sort().filter(Boolean);
  var c2 = config2.split('\n').sort().filter(Boolean);
  // var c3 = c1.map( (el, i) => el + c2[1] )
  return c1.map( (el, i) => el + c2[1] ).filter( (c3El, i ) =>  c1[i] != c2[i] )
}


function diff(config1, config2){
  var c1 = config1.split('\n').sort().filter(Boolean);
  var c2 = config2.split('\n').sort().filter(Boolean);
  return c1.map( (el, i) => (el != c2[i]) && (el + ' :: ' +  c2[1]) ).filter(Boolean)
  // return c1.map( (el, i) => el + c2[1] ).filter( (c3El, i ) =>  c1[i] != c2[i] )
}