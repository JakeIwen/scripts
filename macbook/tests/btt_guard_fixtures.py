"""Small independent SQLite/export fixtures for end-to-end Guard workflows."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from macbook.bettertouchtool.btt_common import MEDIA


def create_guard_database(directory):
    path = Path(directory)/'btt_data_store.version_6_885_build_fixture'
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE ZBTTBASEENTITY (
            Z_PK INTEGER,Z_ENT INTEGER,ZUNIQUEIDENTIFIER TEXT,ZBUNDLEIDENTIFIER TEXT,
            ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
            ZGESTURETYPE INTEGER,ZENABLEDNEW INTEGER,ZORDER INTEGER,ZACTION INTEGER,
            ZACTIONCATEGORY INTEGER,ZICONDATA3 BLOB,ZACTIONDATA TEXT,ZLAUNCHPATH TEXT,
            ZADDITIONALACTIONSTRING TEXT,ZSHORTCUT TEXT,ZGESTURECONFIG TEXT,ZACTIVATED INTEGER,ZNAME3 TEXT)''')
        connection.executescript('''CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER,Z_NAME TEXT);
            INSERT INTO Z_PRIMARYKEY VALUES (2,'App'),(9,'Gesture');
            CREATE TABLE Z_2APPS_GESTURES (Z_2GESTURES INTEGER,Z_9APPS_GESTURES INTEGER);
            CREATE TRIGGER clear_scope AFTER DELETE ON ZBTTBASEENTITY BEGIN
                DELETE FROM Z_2APPS_GESTURES WHERE Z_9APPS_GESTURES=OLD.Z_PK;
            END;
            INSERT INTO ZBTTBASEENTITY (Z_PK,Z_ENT,ZUNIQUEIDENTIFIER,ZBUNDLEIDENTIFIER)
                VALUES (300,2,'app-uuid','BT.G');''')
        base = dict(Z_ENT=9,ZPARENT=None,ZBELONGSTOPRESET2=100,ZGESTURETYPE=0,ZENABLEDNEW=1,
                    ZORDER=0,ZACTION=366,ZACTIONCATEGORY=0,ZSHORTCUT='-1')
        configs = {'root': {'BTTMenuVisibility':0,'BTTMenuModifierKeys':1835008},
                   'button': {'BTTMenuAttributedText':'a private label'}}
        rows = [dict(Z_PK=100,ZUNIQUEIDENTIFIER='preset',ZNAME3='Master',ZACTIVATED=2),
                dict(Z_PK=1,ZUNIQUEIDENTIFIER=MEDIA,ZGESTURETYPE=767,
                     ZICONDATA3=b'\x01'+json.dumps(configs['root']).encode()),
                dict(Z_PK=2,ZUNIQUEIDENTIFIER='button',ZPARENT=1,ZGESTURETYPE=773,
                     ZICONDATA3=b'\x01'+json.dumps(configs['button']).encode()),
                dict(Z_PK=3,ZUNIQUEIDENTIFIER='action',ZPARENT=2,ZGESTURETYPE=-1,
                     ZACTION=248,ZORDER=119,ZLAUNCHPATH='Sample Task'),
                dict(Z_PK=4,ZUNIQUEIDENTIFIER='named',ZGESTURETYPE=643,ZACTION=206,
                     ZGESTURECONFIG='Sample Task',ZLAUNCHPATH='never-run-this',
                     ZADDITIONALACTIONSTRING='/bin/zsh:::-c:::-:::')]
        for addition in rows:
            row = {**base, **addition}
            connection.execute('INSERT INTO ZBTTBASEENTITY ('+','.join(row)+') VALUES ('+
                               ','.join('?' for _ in row)+')',tuple(row.values()))
            if row['Z_PK'] != 100:
                connection.execute('INSERT INTO Z_2APPS_GESTURES VALUES (?,?)',(300,row['Z_PK']))
    exports = [{'BTTUUID':MEDIA,'BTTTriggerType':767,'BTTMenuConfig':configs['root'],
        'BTTMenuItems':[{'BTTUUID':'button','BTTTriggerType':773,'BTTMenuConfig':configs['button'],
            'BTTMenuItemActions':[{'BTTUUID':'action','BTTIsPureAction':True,'BTTPredefinedActionType':248,
                                   'BTTNamedTriggerToTrigger':'Sample Task','BTTOrder':119}]}]},
        {'BTTUUID':'named','BTTTriggerType':643,'BTTTriggerName':'Sample Task','BTTPredefinedActionType':206,
         'BTTShellTaskActionScript':'never-run-this','BTTShellTaskActionConfig':'/bin/zsh:::-c:::-:::'}]
    return path, deepcopy(exports)
