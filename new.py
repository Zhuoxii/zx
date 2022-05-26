# -*- coding: utf-8 -*-
"""Assignment2.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1YJNRT2dwVl68QCqueI5OElPsBQ0KJ7pY
"""

from pyspark.sql.functions import explode, col
from pyspark.sql.functions import udf
from pyspark.sql.functions import lit
from pyspark.sql.functions import locate
from pyspark.sql.functions import size
from pyspark.sql import functions as f
from pyspark.sql.types import StructType, StructField, LongType, StringType, ArrayType, IntegerType
from pyspark.sql.window import Window
from pyspark.sql.functions import count
import argparse

from pyspark.sql import SparkSession
spark = SparkSession \
    .builder \
    .appName("Comp5349 Assignment2") \
    .getOrCreate()



spark.sparkContext.setLogLevel("ERROR")
parser = argparse.ArgumentParser()
parser.add_argument("--output", help="the output path",
                        default='a2_out')
args = parser.parse_args()
output_path = args.output

    # s3://comp5349-2022/test.json
    # s3://comp5349-2022/train_separate_questions.json
    # s3://comp5349-2022/CUADv1.json
data = "s3://comp5349-2022/test.json"
df= spark.read.option('multiline', 'true').json(data)



df_title = df.select(explode("data.title").alias("title")).withColumn("index",f.monotonically_increasing_id())
df_para = df.select(explode("data.paragraphs").alias("data")).withColumn("index",f.monotonically_increasing_id())


df = df_title.join(df_para,df_title.index==df_para.index).drop("index")

df = df.head(5)
df = spark.createDataFrame(df)
df.show()

df = df.select('title','data',explode("data.context").alias("context"))\
      .select('title','data', 'context', explode("data.qas").alias("qas"))\
      .select('title', 'context', 'qas', explode("qas").alias("qas2"))\
      .select('title', 'context', 'qas2', "qas2.question", "qas2.answers","qas2.is_impossible").cache()


def segmentToSequence(data):
  ls = []
  ix = []
  n = 0
  for i in range(len(data)):
    if i >= 4096 and i % 2048 == 0:
      res = list(data[n:i])
      res = "".join(res)
      ls.append(res)
      ix.append([n,i])
      n += 2048
  return ls



udf1=udf(segmentToSequence, ArrayType(StringType()))

### impossible negative
  
df_no_answer = df.filter(df.is_impossible == True)  #.select('context','question','answers' ,"is_impossible")
df_impossible_negative = df_no_answer.withColumn('list_context',udf1(df_no_answer.context))\
                       .withColumn("source",f.explode('list_context'))\
                       .withColumn('type', lit('impossible negative'))\
                       .withColumn('answer_start', lit(0))\
                       .withColumn('answer_end', lit(0))\
                       .select('title','context', 'source', 'question', 'answer_start', 'answer_end', 'type')

# df_impossible_negative.show()

## 筛选出没有impossible negative的sample
df_no_answer = df.filter(df.is_impossible == True).select('title', 'context','question','answers' ,"is_impossible")
# df_no_answer.show()

df_answer = df.withColumn('answers2', explode('answers').alias('answers2'))\
          .select('title','context','question','answers2.text', 'answers2.answer_start',"is_impossible")
df_answer = df_answer.withColumn('list_context',udf1('context'))\
          .withColumn("source",f.explode('list_context')).select( 'title','context', 'source', 'question','text', 'answer_start') #.cache()
# df_answer.show()

def is_positive(record):
  context = record[1]
  source = record[2]
  text = record[4]
  answer_start = record[5]

  source_start = context.index(source)
  source_end = source_start + len(source) 

  answer_end = answer_start + len(text)
  if answer_start <= source_start and answer_end >= source_start:
    return  record + ["positive"]
  elif answer_start >= source_start and answer_start <= source_end:
    return  record + ["positive"]
  else:
    return  record + ["possible negative"]



def positive_answer_index(record):
    context = record[1]
    source = record[2]
    text = record[4]
    answer_start = record[5]
    source_start = context.index(source)
    source_end = source_start + len(source)
    answer_end = answer_start + len(text)

    if answer_start < source_start and answer_end < source_end:
      return    [record[0], record[2], record[3], 0, len(text), record[6]]
    elif answer_start < source_start and answer_end > source_end:
      return  record + [0,len(source)]
    elif answer_start > source_start and answer_end < source_end:
      return  [record[0],  record[2], record[3], source.index(text), source.index(text) + len(text),record[6]]
    else:
      new_text = context[answer_start: source_end]
      return  [record[0], record[2], record[3], source.index(new_text), len(source),record[6]]


def negative_answer_index(record):
    record = [record[0], record[2], record[3], 0, 0, record[6]]
    return record

rdd_answer = df_answer.rdd.map(list)
rdd_type = rdd_answer.map(is_positive)
rdd_positive = rdd_type.filter(lambda x: x[6] == 'positive').map(positive_answer_index)
rdd_possible_negative = rdd_type.filter(lambda x: x[6] == 'possible negative') .map(negative_answer_index)

schema1 =  ['title', 'source', 'question', 'answer_start','answer_end','type']
df_positive = rdd_positive.toDF(schema1).cache()
#df_positive  = spark.createDataFrame(rdd_positive,['context', 'source', 'question','text', 'answer_start','answer_end','type'])
schema2 = ['title', 'source', 'question', 'answer_start','answer_end','type']
df_possible_negative = rdd_possible_negative.toDF(schema2).cache()

"""# Balance negative and positive samples"""

#对于每个contract每个question有多少sample
##对于positive而言
# df_1 = df_positive.groupBy('question').count().withColumnRenamed('count', 'extract_length')
# df_1.show()

df_1 = df_positive.groupBy('question').count().withColumnRenamed('count','question_count')
df_3 = df_positive.groupBy('question').agg(f.countDistinct('title')).withColumnRenamed('count(title)','other_contract_count')
df_4 = df_1.join(df_3, 'question','inner')
df_1 = df_4.withColumn('extract_length',f.round(f.col('question_count')/f.col('other_contract_count'),0).astype('int'))

## 把postive的question和impossible negative的question join
df_2 =  df_1.join(df_impossible_negative, 'question', 'inner').orderBy('title','question','source')\
          .select('title', 'question','source', 'answer_start', 'answer_end','extract_length','type')

# df_2.show()

window1 = Window.partitionBy("title").orderBy('title','question')
df_3 = df_2.groupBy('title','question','extract_length').agg(f.collect_set('source').alias('source_list')).orderBy('title','question')
df_3 = df_3.withColumn('seq_len', f.size('source_list'))\
          .withColumn('lag_extract_length', f.lag(f.col('extract_length')).over(window1))\
          .fillna(0)
df_3 = df_3.withColumn('cusum_lag_extract_length', f.sum(f.col('lag_extract_length')).over(window1))\
          .withColumn('extract_start', f.col('cusum_lag_extract_length')+1)\
          .drop('lag_extract_length', 'cusum_lag_extract_length')\
          .select('title','question','source_list','extract_start','extract_length','seq_len')
#df_3.show()

def new_extract(extract_start, extract_length, seq_len):
  if extract_start <= seq_len and extract_start + extract_length <= seq_len + 1 :
    extract_start2 = extract_start
    extract_length2 = extract_length
  
  elif extract_start <= seq_len and extract_start + extract_length > seq_len + 1:
    extract_start2 = extract_start
    extract_length2 = seq_len - extract_start + 1

  elif extract_start > seq_len and extract_length <= seq_len:
    extract_start2 = 1
    extract_length2 = extract_length
  
  else:
    extract_start2 = 1
    extract_length2 = seq_len
  return [extract_start2, extract_length2]

udf4 = udf(new_extract, ArrayType(IntegerType()))  
df_4 = df_3.withColumn('extract_start',udf4(f.col('extract_start'), f.col('extract_length'),f.col('seq_len'))[0])\
           .withColumn('extract_length',udf4(f.col('extract_start'), f.col('extract_length'),f.col('seq_len'))[1])
df_4 = df_4.filter(f.col('extract_length') >= 1)
df_4  = df_4.filter(f.col('seq_len') >=1)
df_4.show()

# impossible_negative = df_4.withColumn('extract_source', f.slice("source_list",start=f.col('extract_start'), length=f.col('extract_length')))
# # impossible_negative = impossible_negative.filter(f.size('extract_source') >= 1)
# impossible_negative =  impossible_negative.withColumn('source', explode(f.col('extract_source')))\
#                                   .withColumn('answer_start', lit(0))\
#                                   .withColumn('answer_end', lit(0))\
#                                   .select('source', 'question', 'answer_start', 'answer_end')
# impossible_negative.show()

# """## 平衡 possible negative and postive"""

df1 = df_positive.groupBy('title', 'question').count().withColumnRenamed('count','extract_length')

df2 = df_possible_negative.join(df1, ['title','question'], 'inner')
df3 = df2.groupBy('title','question','extract_length').agg(f.collect_set('source').alias('source_list')).orderBy('title','question')\
          .withColumn('seq_len', f.size('source_list'))\
          .withColumn('lag_extract_length', f.lag(f.col('extract_length')).over(window1))\
          .fillna(0)\
          .withColumn('cusum_lag_extract_length', f.sum(f.col('lag_extract_length')).over(window1))\
          .withColumn('extract_start', f.col('cusum_lag_extract_length')+1)\
          .drop('lag_extract_length', 'cusum_lag_extract_length')\
          .select('title','question','source_list','extract_start','extract_length','seq_len')

df4 = df3.withColumn('extract_start',udf4('extract_start', 'extract_length','seq_len')[0])\
           .withColumn('extract_length',udf4('extract_start', 'extract_length','seq_len')[1])

df4 = df4.filter(f.col('extract_length') >= 1)
df4  = df4.filter(f.col('seq_len') >=1)

# possible_negative = df4.withColumn('extract_source', f.slice("source_list",start=f.col('extract_start'), length=f.col('extract_length')))
# possible_negative = possible_negative.withColumn('source', explode('extract_source'))\
#                                   .withColumn('answer_start', lit(0))\
#                                   .withColumn('answer_end', lit(0))\
#                                   .select('source', 'question', 'answer_start', 'answer_end')
# possible_negative.show()

print("successfully!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

"""# 结果合并"""

# positive = df_positive.select('source', 'question', 'answer_start','answer_end')
# df_all = positive.union(impossible_negative).union(possible_negative)
# # df_all.show()

# import json
# result = df_all.toJSON().collect()
# output = json.dumps(result, indent = 2)
# with open('result.json','w') as f:
#   json.dump(output, f)

spark.stop()

