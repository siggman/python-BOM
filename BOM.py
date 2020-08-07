# MIT License
# 
# Copyright (c) 2020 Rob Siegwart
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''
Build and query multi-level and flattened BOMs based on elemental data stored in
Microsoft Excel files.

Conventions here are based on some of the concepts from the book "Engineering
Documentation Control Handbook", 4th Ed. by Frank B. Watts. Namely:

**a master parts "database" is used**

This is represented by the usage of a separate Excel file named
"Parts list.xlsx" by default, which contains all component items such as parts,
drawings, and specification documents, and any properties. The following
property names have special usage:

- PN        the part or item number
- Item      the type of item, valid options  =>  ['part','assembly','drawing']
'''

import sys
import glob
import os
from collections import Counter
from collections.abc import Set, Collection
import pandas as pd
from anytree import NodeMixin, SymlinkNodeMixin, RenderTree
from tabulate import tabulate


def fn_base(arg):
    '''
    Return the part of a filename without the file extension. ::

        Foo_12.34.xlsx   =>  Foo_12.34

    :param arg:     String or list of strings to remove extension.
    :return:        String or list of strings
    '''
    if isinstance(arg, list):
        return [ fn_base(item) for item in arg ]
    return '.'.join(arg.split('.')[:-1])


class BaseItem:
    '''
    A terminal object in a bill-of-material. Represents a part, drawing, or
    document (not an assembly). Does not have child objects and must contain a
    parent.

    :param PN:              Part or item number (string or number)
    :param BOM parent:      BOM containing this item
    :param str item_type:   A type descriptor
    :param kwargs:          Any other fields
    '''
    children = None

    def __init__(self, PN, parent=None, item_type=None, **kwargs):
        self.PN = PN
        self.parent = parent
        self.item_type = item_type
    
        self.kwargs =kwargs
        for k,v in kwargs.items():
            try:
                setattr(self, k, v)
            except AttributeError:
                continue
        
    @property
    def series(self):
        cols = ['PN','item_type','parent'] + list(self.kwargs.keys())
        return pd.Series({k:getattr(self,k,None) for k in cols})
    
    def __repr__(self):
        return f'Item {self.PN}'
    
    __str__ = __repr__


class Item(BaseItem, NodeMixin):
    pass


class ItemLink(BaseItem, SymlinkNodeMixin):
    def __init__(self, target):
        self.target = target


class BOM(Set, NodeMixin):
    '''
    A bill-of-material. Can be a parent of another BOM or have several child
    BOMs. At minimum there must be a "PN" column denoting the part name and a
    "QTY" column denoting the quantity of that part. Other columns maybe added
    and are passed through.

        PN        Description   QTY
        --------- ------------- -----
        17954-1   Wheel         2
        17954-2   Axle          1

    :param DataFrame data:      input BOM data
    :param str name:            optional BOM name
    :param BOM parent:          another ``BOM`` object which is the parent
                                assembly
    :param list children:       list of ``BOM`` objects which are sub-assemblies
    :param BOM parts_list:      Parts list BOM object
    :param str item_type:       type of object ['part','assembly','document']
    '''
    def __init__(self, df=None, name=None, parent=None, children=None,
                 item_type=None, parts_db=None):
        self.raw_data = df
        self.name = name
        self.parent = parent
        self.children = children or []
        self.item_type = item_type.lower() if item_type else None
        self.parts_db = parts_db

    def __contains__(self, item):
        return item in self.children

    def __iter__(self):
        for item in self.children:
            yield item
    
    def __len__(self):
        return len(self.children)

    @classmethod
    def from_filename(cls, filename, name=None):
        data = pd.read_excel(filename)
        return cls(df=data, name=name or fn_base(os.path.basename(filename)))
    
    @property
    def fields(self):
        return list(self.df.columns)
    
    @property
    def parts(self):
        return [ item for item in self.children if item.item_type == 'part' ]

    @property
    def assemblies(self):
        return [ item for item in self.children if item.item_type == 'assembly' ]
    
    @property
    def flat(self):
        '''
        Return a flattened version of the BOM, with each sub-assembly contained
        in it expanded.
        '''
        items = self.parts
        for assem in self.assemblies:
            items += assem.flat
        return items
    
    @property
    def quantities(self):
        return dict(Counter(self.flat))

    @property
    def tree(self):
        return str(RenderTree(self))

    # def parse_data(self):
    #     '''Convert rows in DataFrame to class objects.'''
    #     f
    
    @classmethod
    def from_folder(cls, directory, parts_file='Parts list'):
        '''
        Generate a hierarchial BOM from a folder containing .xlsx files. The
        xlsx file with the same name as parameter ``parts_file`` is taken as the
        master parts list. All others are treated as sub-assemblies. The root
        BOM is discovered (there should only be one or an exception is raised)
        via inter-BOM references and each non-root BOM is assigned children and
        a parent. Each item not an assembly is converted to an ``Item`` object.

        :param str directory:   The source directory containing BOM files.
        :param str parts_file:  The name of the master parts list Excel file.
                                Default is ``Parts list.xlsx``.
        :return BOM:            Returns a top-level BOM with all sub-assemblies
                                as child BOMs.
        '''
        files = [ os.path.split(fn)[-1] for fn in glob.glob(os.path.join(directory, '*.xlsx')) ]
        assembly_files = [ x for x in files if fn_base(x).lower() != parts_file.lower() ]

        assemblies = { fn_base(file):BOM.from_filename(os.path.join(directory, file)) for file in assembly_files }
        parts_bom = BOM.from_filename(os.path.join(directory, f'{parts_file}.xlsx'))
        parts = { row.PN:Item(**{**row.to_dict(), **{'item_type': 'part'}}) for i,row in parts_bom.raw_data.iterrows() }

        # Assign parent/child relationships
        for name,bom in assemblies.items():
            children = []
            for i,row in bom.raw_data.iterrows():
                if row.PN in assemblies:                    # it is an assembly
                    sub_bom = assemblies.get(row.PN)
                    sub_bom.parent = bom
                    sub_bom.item_type = 'assembly'
                    children.append(sub_bom)
                else:                                       # it is a part
                    try:
                        part_ = parts[row.PN]
                    except IndexError:
                        print(f'Unable to find part "{row.PN}" in {parts_file+".xlsx"}')
                        continue
                    if part_.parent:                        # multi-use part and has already has been placed in an assembly
                        sym_part = ItemLink(target=part_)   # therefore make any new copies of this part symlink objects
                        children.append(sym_part)
                    else:
                        children.append(part_)
            bom.children = children

        # Find root
        root = [ bom for bom in assemblies.values() if bom.is_root ]
        if len(root) > 1:
            raise Exception('Singular root BOM not found.')
        if len(root) == 0:
            raise Exception('No root BOM found.')
        root = root[0]
        root.parts_db = parts
        return root
    
    def __repr__(self):
        return self.name if self.name else f'BOM with {len(self.raw_data)} items'
    
    __str__ = __repr__