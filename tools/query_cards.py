"""Search the decoded TF1 database. Example: python query_cards.py 'Star Boy'."""
import argparse,json,sqlite3
from card_database import ROOT
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('query',nargs='?',default='')
    p.add_argument('--id',type=lambda x:int(x,0))
    p.add_argument('--limit',type=int,default=20)
    a=p.parse_args()
    with sqlite3.connect(ROOT/'reports/card_database/cards.sqlite') as con:
        con.row_factory=sqlite3.Row
        rows=con.execute('SELECT * FROM cards WHERE '+('card_id=?' if a.id is not None else 'name LIKE ?')+' ORDER BY card_id LIMIT ?',[(a.id if a.id is not None else '%'+a.query+'%'),a.limit])
        for row in rows:
            c=dict(row); atk='?' if c['attack'] is None else c['attack'];deff='?' if c['defense'] is None else c['defense']
            print(f"{c['card_id']} ({c['card_id_hex']}) | {c['name']} | {c['card_type']}")
            if c['kind_code']<13:print(f"  {c['attribute']} {c['monster_type']}, Level {c['level']}, ATK {atk}, DEF {deff}")
            else:print('  '+str(c['spell_trap_icon']))
            print('  '+c['description'])
            print('  Password: '+str(c['password_text']))
if __name__=='__main__':main()
