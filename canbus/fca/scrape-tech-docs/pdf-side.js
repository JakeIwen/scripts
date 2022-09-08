// try to get urls of opened pages 
var urls = []
var h = true
var ms = 600

stop = () => h = false

var ivl = setInterval(function(){
  if (!h) {
    clearInterval(ivl)
    urls = [...new Set(urls)]
    console.log('urls:', urls)
  }
    urls.push(document.URL)
}, ms);

// web/secure/disciplines?config-level=YEAR_MODEL_ENGINE&config-id=09994f8e-b096-11e4-bd44-22000ae11964&lang=en_US&contentId=baf597f7-a5bf-421b-9ef1-6f6fcd9ee0cd"
// connect/api/content/pdf/baf597f7-a5bf-421b-9ef1-6f6fcd9ee0cd/YEAR_MODEL_ENGINE/09994f8e-b096-11e4-bd44-22000ae11964?X-Auth-Token=4a54efbeaa6143789617bbd504468e9c&locale=en_US&printOptions=%7B%22pageScaling%22%3A%22MULTIPLE_PAGES%22%2C%22orientation%22%3A%22PORTRAIT%22%2C%22pageSize%22%3A%22LETTER%22%7D"

// Make sure to include the following in the header:
// 
// <script src='https://cdn.jsdelivr.net/npm/pdf-lib/dist/pdf-lib.js'></script>
// <script src='https://cdn.jsdelivr.net/npm/pdf-lib/dist/pdf-lib.min.js'></script>
// Then:

async function mergeAllPDFs(urls) {
    
    const pdfDoc = await PDFLib.PDFDocument.create();
    const numDocs = urls.length;
    
    for(var i = 0; i < numDocs; i++) {
        const donorPdfBytes = await fetch(urls[i]).then(res => res.arrayBuffer());
        const donorPdfDoc = await PDFLib.PDFDocument.load(donorPdfBytes);
        const docLength = donorPdfDoc.getPageCount();
        for(var k = 0; k < docLength; k++) {
            const [donorPage] = await pdfDoc.copyPages(donorPdfDoc, [k]);
            //console.log("Doc " + i+ ", page " + k);
            pdfDoc.addPage(donorPage);
        }
    }

    const pdfDataUri = await pdfDoc.saveAsBase64({ dataUri: true });
    //console.log(pdfDataUri);
  
    // strip off the first part to the first comma "data:image/png;base64,iVBORw0K..."
    var data_pdf = pdfDataUri.substring(pdfDataUri.indexOf(',')+1);
}