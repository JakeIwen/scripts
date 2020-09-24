var express =  require('express');
var app = express();
var path = require('path');
var bodyParser = require('body-parser');
var fs = require('fs-extra');
var request = require('request');

var Nightmare = require('nightmare');
var nightmare = Nightmare({ show: false });





app.use(bodyParser({limit: '50mb'}));

var numFiles = 0;

app.use(bodyParser({limit: '50mb', extended: true}));
app.use(bodyParser.urlencoded({limit: '50mb', extended: true}));

app.post('/pages', function(req, res){
  makeDirectory(req.body, res);
});
app.use(express.static('../public'));

// Catchall route
app.get('/', function (req, res) {
  console.log(path.resolve('./public/views/index.html'));
  res.sendFile(path.resolve('./public/views/index.html'));
});


app.set('port', process.env.PORT || 3000);

app.listen(app.get('port'), function () {
  console.log('Listening on port ', app.get('port'));
});


function makeDirectory(pageData, res) {
  const {page, appName} = pageData
  const dirPath = path.resolve('../dbpages/' + appName)
  console.log('dirPath', dirPath);
  mkdirp(dirPath, function (err) {
    if (err) res(err)
    else writePage(dirPath, pageData, res)
  });
}

async function writePage(dirPath, pageData, res){
  filePath = dirPath + '/' + pageData.name
  const result = fs.writeFile(filePath, pageData.page, function(){
    console.log('file attempted :', numFiles++, filePath);
    
    res.sendStatus(200);
    console.log('sucessfully wrote:', filePath);
  })
}

app.get('/proxy', function(req, res){
  console.log('req', req.url);
  // request(req.url.split('?')[1], function (error, response, body) {
  //   console.log('error:', error); // Print the error if one occurred
  //   console.log('statusCode:', response && response.statusCode); // Print the response status code if a response was received
  //   res.send(body)
  //   // console.log('body:', body); // Print the HTML for the Google homepage.
  // });
  // 
  nightmare.goto(req.url.split('?')[1])
  .evaluate(function(){
      return document.body.outerHTML;
  })
  .end()
  .then(function (result) {
          console.log(result)
          res.send(result)
   })
});

var options = {
  host: 'www.google.com',
  port: 80,
  path: '/index.html'
};

