
var data = JSON.parse(process.argv[2])
var tracks = data.tracks

// var num_media_tracks = tracks.filter(t => t.type !== 'subtitles').length
var track = tracks.find(t => {
  var props = t.properties
  return (t.type === 'subtitles' && props.forced_track === false && props.language === 'eng')
})
console.log(track.id)