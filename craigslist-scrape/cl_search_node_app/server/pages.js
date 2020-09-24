var express = require('express');
var router = express.Router();
var fs = require('fs-extra');
var mkdirp = require('mkdirp')
var path = require('path');


router.post('/', function(req, res) {
  console.log('request hit', console.log(res));
  
  makeDirectory(req.body);
});

function makeDirectory(pageData) {
  const {page, appName} = pageData
  const dirPath = path.resolve('../' + appName.replace(' ', ''))
  console.log('dirPath', dirPath);
  mkdirp(dirPath, function (err) {
    if (err) console.error(err)
    else writePage(dirPath, page)
  });
}

async function writePage(dirPath, page){
  filePath = dirPath + '/' + page.name
  const result = await fs.writeFile(filePath, page.text)
  console.log('sucessfully wrote:');
  console.log(filePath);
}

// async function asyncForEach(array, callback) {
//   for (let index = 0; index < array.length; index++) {
//     await callback(array[index], index, array)
//   }
// }

module.exports = router;
