//our root app component
import {
  Component,
  NgModule,
  OnInit,
  ViewChild,
  ViewContainerRef
} from '@angular/core'
import { BrowserModule } from '@angular/platform-browser'

import { Service } from './service'
import { DynamicComponent } from './dynamic.component'

@Component({
  selector: 'my-app',
  template: `
    <div>
      <h2>Hello {{name}}</h2>
      <template #dynamic></template>
    </div>
  `,
})
export class App implements OnInit {
  name = 'from Angular'
  private service: any = {};

  @ViewChild('dynamic', { read: ViewContainerRef }) viewContainerRef: ViewContainerRef

  //constructor(service: Service, viewContainerRef: ViewContainerRef) {
  constructor(service: Service) {
    this.service = service
  }

  ngOnInit() {
    this.service.setRootViewContainerRef(this.viewContainerRef)
    this.service.addDynamicComponent()
  }
}

@NgModule({
  imports: [ BrowserModule ],
  declarations: [
    App,
    DynamicComponent
  ],
  providers: [ Service ],
  entryComponents: [ DynamicComponent ],
  bootstrap: [ App ]
})
export class AppModule {}
