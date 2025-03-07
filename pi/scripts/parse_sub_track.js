
var data = JSON.parse(process.argv[2])
var tracks = data.tracks.filter(t => t.type === 'subtitles')

// var num_media_tracks = tracks.filter(t => t.type !== 'subtitles').length
var idx = tracks.findIndex(t => {
  var props = t.properties
  return (props.forced_track === false && props.language === 'eng')
})
console.log(idx)