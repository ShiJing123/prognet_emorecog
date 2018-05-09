# coding: utf-8
# %load run_rnn.py

import torch
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
import pickle as pk
import numpy as np
import torch.nn as nn
import pandas as pd
import pickle

# custom
from utils import to_cu
from utils import init_weights

class EmoLLDDataset(Dataset):

    def __init__(self, pk_path, ifold, mode, catweight, is_cuda):

        self.ifold = ifold

        self.is_cuda = is_cuda

        df_table = pickle.load(open(pk_path, 'rb'))

        self.cat2dec = {'N':0, 'S':1, 'A':2, 'H':3}

        self.catweight = catweight

        if mode == 'train':

            self.df = df_table[ifold][0]

        elif mode == 'test':

            self.df = df_table[ifold][1]

        elif mode == 'valid':

            self.df = df_table[ifold][2]

        else:

            print('error in mode in EmoLLDDataset')

    def __len__(self):

        return len(self.df)

    def __getitem__(self, idx):

#        return {
#                'lld':to_cu(self.is_cuda,torch.FloatTensor(pickle.load(open(df.iloc[idx]['lldpkpath'],'rb')))),
#                'cat':to_cu(self.is_cuda,torch.LongTensor([self.cat2dec[df.iloc[idx]['cat']]]))}
        return to_cu(self.is_cuda,torch.FloatTensor(pickle.load(open(self.df.iloc[idx]['lldpkpath'],'rb')))), to_cu(self.is_cuda,torch.LongTensor([self.cat2dec[self.df.iloc[idx]['cat']]])), to_cu(self.is_cuda,torch.FloatTensor([self.catweight[self.cat2dec[self.df.iloc[idx]['cat']]]]))

def partial_collate_fn(data, is_cuda):

#    print(data)
    data.sort(key=lambda x: np.shape(x[0])[0], reverse=True)
    llds, cats, score_weights = zip(*data)
#    print(llds)
#    print(cats)
#    print(len(llds))
#    print(len(cats))

    max_len = 0

    for i, lld in enumerate(llds):
#        print(lld)
#        print(np.shape(lld))        
        if np.shape(lld)[0] > max_len:

            max_len = np.shape(lld)[0]

#    print(max_len)
    lens = [np.shape(lld)[0] for lld in llds]

    padded_llds = torch.FloatTensor(np.zeros((len(llds), max_len, 32)))
    
    for i, lld in enumerate(llds):
        cur_len = np.shape(lld)[0]       

        padded_llds[i, 0:cur_len, :] = lld

#    print(max_len)

    return to_cu(is_cuda,padded_llds), to_cu(is_cuda,torch.stack(cats)), to_cu(is_cuda, torch.stack(score_weights)), lens


import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.nn.utils.rnn import pack_padded_sequence
from torch.nn.utils.rnn import pad_packed_sequence

class local_att_rnn(nn.Module):

    def __init__(self, is_cuda):
        super(local_att_rnn, self).__init__()

        self.is_cuda = is_cuda

        self.fc1 = nn.Linear(32, 512)
        #self.fc1 = nn.Linear(32, 256)

        self.fc2 = nn.Linear(512, 512)
        #self.fc2 = nn.Linear(256, 256)

        #self.blstm = torch.nn.LSTM(256, 64, 1, 
        self.blstm = torch.nn.LSTM(512, 128, 1, 
                batch_first=True,
                dropout=0.5,
                bias=True,
                bidirectional=True)

        #self.u = to_cu(is_cuda, Variable(torch.zeros((128,))))
        self.u = to_cu(is_cuda, Variable(torch.zeros((256,))))

        #self.fc3 = nn.Linear(128, 4)
        self.fc3 = nn.Linear(256, 4)

        if self.is_cuda is True:
            self.cuda()

        self.apply(init_weights)

    def forward(self, x, lens):

        batch_size = x.size()[0]

        batch = Variable(x)

        indep_feats = batch.view(-1, 32) # reshape(batch) 

        #print('indep_feats:',indep_feats.size())

        indep_feats = F.relu(F.dropout(self.fc1(indep_feats)))

        indep_feats = F.relu(F.dropout(self.fc2(indep_feats)))

        batched_feats = indep_feats.view(batch_size, -1, 512)
        #batched_feats = indep_feats.view(batch_size, -1, 256)

        #print('batched_feats:',batched_feats)

        #print('batch_size:',batch_size)

        packed = pack_padded_sequence(batched_feats, lens, batch_first=True) 

        #print('packed:',packed)

        output, hn = self.blstm(packed)

        #print('output:',output)

        padded, lens = pad_packed_sequence(output, batch_first=True, padding_value=0.0)

        #print('padded:',padded)

        #print('self.u:', self.u)

        alpha = F.softmax(torch.matmul(padded, self.u))

        #print('alpha:',alpha)

        # weighted = alpha * padded

        #print('alpha * padded:', weighted)

        #print('torch.cumsum:',torch.sum(torch.matmul(alpha, padded), dim=1))

        return F.softmax(F.dropout(self.fc3(torch.sum(torch.matmul(alpha, padded), dim=1))))


import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F

from sklearn.metrics import recall_score
from torch.utils.data import DataLoader
from tqdm import tqdm

import os

def validate(validset, network, batch_size, criterion, score_type, is_cuda):

    collate_fn = partial(partial_collate_fn,is_cuda=is_cuda)

    dataloader = DataLoader(validset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn)

    loss = 0.0

    score = 0.0

    network.eval()

    for i, batch in enumerate(tqdm(dataloader, total=len(dataloader))):

        padded, cats, score_weight, lens = batch

        output = network.forward(padded, lens)

        y_true = Variable(cats).view(-1)
        score_weight = Variable(score_weight).view(-1)

        loss += criterion(output, y_true).data

        y_pred = output.max(dim=1)[1]

        if score_type is 'ua':

            average_type = 'macro'

        elif score_type is 'wa':

            average_type = 'weighted'

        else:

            average_type = 'error'
            print('error in score_type')

        score += recall_score(
                y_pred.data.cpu().numpy(),
                y_true.data.cpu().numpy(),
                    average=average_type,
                    sample_weight=score_weight.cpu().numpy())

    return loss/(i+1), score/(i+1)

def test(testset, network, batch_size, criterion, score_type, is_cuda):

    collate_fn = partial(partial_collate_fn,is_cuda=is_cuda)

    dataloader = DataLoader(testset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn)

    loss = 0.0

    score = 0.0

    network.eval()

    for i, batch in enumerate(tqdm(dataloader, total=len(dataloader))):
        
        padded, cats, score_weight, lens = batch

        output = network.forward(padded, lens)

        y_true = Variable(cats).view(-1)

        score_weight = Variable(score_weight).view(-1)

        loss += criterion(output, y_true).data

        y_pred = output.max(dim=1)[1]

        if score_type is 'ua':

            average_type = 'macro'

        elif score_type is 'wa':

            average_type = 'weighted'

        else:

            average_type = 'error'
            print('error in score_type')

        score += recall_score(
                y_pred.data.cpu().numpy(),
                y_true.data.cpu().numpy(),
                    average=average_type,
                    sample_weight=score_weight.cpu().numpy())

    return loss/(i+1), score/(i+1)


class Trainer():

    def __init__(self, expname, traindataloader, validset, batch_size, network, st_epoch, ed_epoch, optimizer, criterion, score_type, is_cuda):

        self.expname = expname

        self.traindataloader = traindataloader

        self.validset = validset

        self.batch_size = batch_size

        self.network = network

        self.st_epoch = st_epoch

        self.ed_epoch = ed_epoch

        self.optimizer = optimizer

        self.criterion = criterion

        self.train_losses = []

        if score_type in ['wa', 'ua']:
            
            self.score_type = score_type

        else:
            
            print('error in score_type')

        self.val_scores = []

        self.is_cuda = is_cuda



    def training(self):

        best_valid_score = 0.0

        for epoch in range(self.st_epoch, self.ed_epoch):

            for i, batch in enumerate(tqdm(self.traindataloader, total=len(self.traindataloader))):

                padded, cats, _, lens = batch

                y_true = Variable(cats).view(-1)

                self.optimizer.zero_grad()

                self.network.train()

                output = self.network.forward(padded, lens)

                train_loss = self.criterion(output, y_true)

#In [12]: x = Variable(torch.randn(10), requires_grad=True)

#In [13]: y = x ** 2

#In [14]: grad = torch.randn(10)

#In [15]: torch.autograd.backward([y], [grad])

                #torch.autograd.backward([output], [train_loss])
                train_loss.backward()

                self.optimizer.step()

            valid_loss, valid_score = validate(
                    self.validset, self.network, self.batch_size,
                    self.criterion, self.score_type, self.is_cuda)

            train_log = '[%d, %5d] loss: %.3f'%(epoch, i, train_loss.data[0])
            valid_log = '[%d, %5d] valid_score: %s'%(epoch, i, str(valid_score))
            os.system('echo %s >> %s.log'%(train_log, self.expname))
            os.system('echo %s >> %s.log'%(valid_log, self.expname))
            print(train_log)

            filename = self.expname + '.pth'

            if valid_score > best_valid_score:

                best_valid_score = valid_score
                state = {
                        'net_state_dict':self.network.state_dict(),
                        'train_loss':train_loss,
                        'valid_score':valid_score,
                        'optim_state_dict':self.optimizer.state_dict()}

                valid_log = '[%d, %5d] best valid_score: %s'%(epoch, i, str(best_valid_score))

                os.system('echo best: %s >> %s.log'%(valid_log, self.expname))
                print(valid_score)

        torch.save(state, filename)
        print('save model',filename)
        print('Finished Training')              

from functools import partial
import random
import sys
import pandas as pd

expname = sys.argv[1]

i_fold = int(sys.argv[2])
print("i_fold:",i_fold)

batch_size = 100

score_type = 'wa'

dataset_path = 'iemocap_5sessions_for_cv_frac_0.1.df.pk'

epoch_st = 0

epoch_ed = 300

is_cuda = True

df = pd.read_hdf('iemocap_4emo.h5','table')

catweight = torch.Tensor(pickle.load(open('iemocap_balanced_weights.pk','rb')))

#catweight = torch.Tensor([
#                len(df)/len(df.loc[df.loc[:,'cat']=='N']),
#                len(df)/len(df.loc[df.loc[:,'cat']=='S']),
#                len(df)/len(df.loc[df.loc[:,'cat']=='A']),
#                len(df)/len(df.loc[df.loc[:,'cat']=='H'])])
print(catweight)

lldtrainset = EmoLLDDataset(dataset_path, i_fold, 'train', catweight, is_cuda)
lldvalidset = EmoLLDDataset(dataset_path, i_fold, 'valid', catweight, is_cuda)

collate_fn = partial(partial_collate_fn,is_cuda=is_cuda)

trainloader = DataLoader(lldtrainset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn)

model = local_att_rnn(is_cuda)

optimizer = torch.optim.Adam(model.parameters(), lr=0.00001)

criterion = nn.CrossEntropyLoss(weight=to_cu(is_cuda,Variable(catweight)))

trainer = Trainer(expname+'_'+ str(i_fold), trainloader, lldvalidset, batch_size, model, epoch_st, epoch_ed, optimizer, criterion, score_type, is_cuda)

trainer.training()

lldtestset = EmoLLDDataset(dataset_path, i_fold, 'test', catweight, is_cuda)

best_model = local_att_rnn(is_cuda)

best_model.load_state_dict(torch.load(expname + '_' + str(i_fold)+'.pth')['net_state_dict'])

test_loss, test_score = test(lldtestset, best_model, batch_size, criterion, score_type, is_cuda)

test_log = 'test_score: %s'%(str(test_score))

os.system('echo %s >> %s'%(test_log, expname + '_' + str(i_fold) + '.log'))

